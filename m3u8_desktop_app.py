from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
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
from urllib.parse import parse_qs, urlparse

from browser_companion import BrowserCompanionError, BrowserInbox
from m3u8_core import (
    BaiduPanDownloadJob,
    CoalescingEventBuffer,
    DirectDownloadJob,
    DownloadHistoryStore,
    DownloadJob,
    DownloadPreferences,
    DownloadRecord,
    HlsError,
    UserFacingError,
    VideoCandidate,
    YouTubeDownloadJob,
    candidate_score,
    classify_error,
    default_history_path,
    discover_candidates,
    ffmpeg_capability,
    find_baidupcs_executable,
    load_best_media_playlist,
    make_headers,
    media_identity_url,
    redact_url,
    redact_sensitive_text,
    refresh_pikpak_candidate,
    sanitize_file_name,
)


APP_NAME = "Universal Video Downloader"
APP_TITLE = "通用视频下载器"
UI_REFRESH_INTERVAL_MS = 100
BROWSER_INBOX_INTERVAL_MS = 1000
MAX_SEGMENT_BLOCKS = 160
MAX_LOG_LINES = 2000
BATCH_ITEM_MAX_ATTEMPTS = 3
BATCH_ITEM_RETRY_BACKOFF_SECONDS = 1.5
HISTORY_RETRY_STATES = frozenset({"queued", "failed", "stopped", "interrupted"})


class UniversalVideoDownloaderApp(tk.Tk):
    """Windows desktop shell for media discovery, download control, and local task history."""

    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1220x840")
        self.minsize(1040, 720)
        self._apply_window_icon()

        self.event_buffer = CoalescingEventBuffer()
        self.browser_inbox = BrowserInbox()
        self.candidates: list[VideoCandidate] = []
        self.current_job: BaiduPanDownloadJob | DirectDownloadJob | DownloadJob | YouTubeDownloadJob | None = None
        self.current_candidate: VideoCandidate | None = None
        self.current_record_id = ""
        self.pending_history_retry: DownloadRecord | None = None
        self.history_retry_record_id = ""
        self.download_thread: threading.Thread | None = None
        self.partial_export_job: DownloadJob | None = None
        self.queue_stop_event = threading.Event()
        self.queue_total = 0
        self.queue_completed = 0
        self.queue_failed = 0
        self.subtitle_choices: dict[str, tuple[str, bool]] = {}
        self.is_downloading = False
        self.is_analyzing = False
        self.advanced_visible = False
        self._input_url = ""
        self._analysis_url = ""
        self.analyzed_url = ""
        self._selection_ids: tuple[str, ...] = ()
        self._candidate_ids: tuple[str, ...] = ()
        self._filter_after_id = None
        self._motion_after_id = None
        self._progress_target = 0.0
        self._progress_busy = False
        self._progress_paused = False
        self._queue_entries: list[tuple[VideoCandidate, Path, str]] = []
        self._queue_source = ""
        self._queue_requires_code = False

        self.history_store = DownloadHistoryStore(default_history_path())
        self.history_records = self._load_history()
        self.last_history_write = 0.0
        self.current_progress_value = 0.0
        self.current_bytes_done = 0

        self.segment_total = 0
        self.segment_block_count = 0
        self.segment_items: dict[int, int] = {}
        self.segment_status: dict[int, str] = {}
        self._dirty_segment_blocks: set[int] = set()

        self.last_progress_bytes = 0
        self.last_progress_done = 0
        self.last_progress_time = time.monotonic()
        self.smoothed_speed = 0.0
        self.smoothed_unit_rate = 0.0

        default_dir = Path.home() / "Downloads" / "Video Downloader"
        self.url_var = tk.StringVar()
        self.referer_var = tk.StringVar()
        self.access_code_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(default_dir))
        self.file_name_var = tk.StringVar(value="video.mp4")
        self.concurrency_var = tk.StringVar(value="8")
        self.keep_cache_var = tk.BooleanVar(value=True)
        self.quality_var = tk.StringVar(value="最佳质量")
        self.subtitle_var = tk.StringVar(value="不下载字幕")
        self.subtitle_format_var = tk.StringVar(value="srt")
        self.auto_subtitle_var = tk.BooleanVar(value=False)
        self.embed_subtitle_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="准备就绪")
        self.selection_var = tk.StringVar(value="输入链接并解析后，这里会显示可下载媒体")
        self.progress_detail_var = tk.StringVar(value="尚未开始任务")
        self.candidate_count_var = tk.StringVar(value="等待解析")
        self.history_query_var = tk.StringVar()
        self.history_filter_var = tk.StringVar(value="全部状态")
        self.history_summary_var = tk.StringVar(value="0 个任务")
        self.history_detail_var = tk.StringVar(value="请选择任务查看状态")
        self.candidate_query_var = tk.StringVar()
        self.candidate_sort_var = tk.StringVar(value="原始顺序")
        self.reduce_motion_var = tk.BooleanVar(value=False)

        self._configure_style()
        self._build_ui()
        self.url_var.trace_add("write", self._on_source_changed)
        self.candidate_query_var.trace_add("write", self._schedule_candidate_filter)
        self._sync_input_controls()
        self._refresh_history()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind_all("<Control-l>", self._focus_url)
        self.bind_all("<Control-Return>", self._shortcut_analyze)
        self.after(UI_REFRESH_INTERVAL_MS, self._drain_events)
        self.after(BROWSER_INBOX_INTERVAL_MS, self._poll_browser_inbox)

    def _load_history(self) -> list[DownloadRecord]:
        records = self.history_store.load()
        changed = False
        restored: list[DownloadRecord] = []
        for record in records:
            if record.status in {"queued", "preparing", "downloading", "paused"}:
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

        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("App.TFrame", background="#F3F4F6")
        style.configure("Surface.TFrame", background="#FFFFFF")
        style.configure("Header.TFrame", background="#111827")
        style.configure("HeaderTitle.TLabel", background="#111827", foreground="#FFFFFF", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("HeaderText.TLabel", background="#111827", foreground="#AEB8C8", font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", background="#FFFFFF", foreground="#15171A", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Body.TLabel", background="#FFFFFF", foreground="#24262A", font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", background="#FFFFFF", foreground="#6B7280", font=("Microsoft YaHei UI", 9))
        style.configure("PageTitle.TLabel", background="#F3F4F6", foreground="#15171A", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("PageText.TLabel", background="#F3F4F6", foreground="#667085", font=("Microsoft YaHei UI", 9))
        style.configure("Badge.TLabel", background="#223047", foreground="#DCE7F7", font=("Microsoft YaHei UI", 8), padding=(9, 4))
        style.configure("Status.TLabel", background="#1D2939", foreground="#D6E4FF", font=("Microsoft YaHei UI", 9, "bold"), padding=(10, 5))
        style.configure("Eyebrow.TLabel", background="#FFFFFF", foreground="#1677FF", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Count.TLabel", background="#FFFFFF", foreground="#667085", font=("Microsoft YaHei UI", 9))

        style.configure(
            "TEntry",
            fieldbackground="#FFFFFF",
            foreground="#17191C",
            bordercolor="#D5D9E0",
            lightcolor="#D5D9E0",
            darkcolor="#D5D9E0",
            padding=(10, 5),
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
        style.configure("Compact.TButton", padding=(8, 4), background="#FFFFFF", foreground="#24262A", borderwidth=1, relief="flat")
        style.map("Compact.TButton", background=[("active", "#F3F5F8"), ("disabled", "#F5F6F8")], foreground=[("disabled", "#A5ABB4")])
        style.configure("CompactDanger.TButton", padding=(8, 6), background="#E5484D", foreground="#FFFFFF", borderwidth=0)
        style.map("CompactDanger.TButton", background=[("active", "#CD3D42"), ("disabled", "#F0B8BA")], foreground=[("disabled", "#FFFFFF")])

        style.configure("TNotebook", background="#F3F4F6", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 6), background="#E6E9EE", foreground="#5F6672", borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", "#FFFFFF")], foreground=[("selected", "#17191C")])
        style.configure("Treeview", rowheight=36, background="#FFFFFF", fieldbackground="#FFFFFF", foreground="#25272B", borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"), background="#F5F6F8", foreground="#687080", relief="flat")
        style.map("Treeview", background=[("selected", "#E7F1FF")], foreground=[("selected", "#15171A")])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Horizontal.TProgressbar", background="#1677FF", troughcolor="#E7EAF0", bordercolor="#E7EAF0", lightcolor="#1677FF", darkcolor="#1677FF")

    def _build_ui(self) -> None:
        header = ttk.Frame(self, style="Header.TFrame", padding=(22, 8))
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

        self.status_badge = ttk.Label(header, textvariable=self.status_var, style="Status.TLabel")
        self.status_badge.pack(side=tk.RIGHT, padx=(8, 0))
        capability_text = "FFmpeg 已就绪" if shutil.which("ffmpeg") else "FFmpeg 未检测到"
        ttk.Label(header, text=capability_text, style="Badge.TLabel").pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Label(header, text="HLS  /  HTTP  /  yt-dlp", style="Badge.TLabel").pack(side=tk.RIGHT)

        shell = ttk.Frame(self, style="App.TFrame", padding=(18, 8, 18, 10))
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

        source = ttk.Frame(tab, style="Surface.TFrame", padding=12)
        source.grid(row=0, column=0, sticky=tk.EW)
        source.columnconfigure(0, weight=1)

        title_row = ttk.Frame(source, style="Surface.TFrame")
        title_row.grid(row=0, column=0, columnspan=4, sticky=tk.EW)
        title_row.columnconfigure(0, weight=1)
        ttk.Label(title_row, text="添加媒体链接", style="Section.TLabel").grid(row=0, column=0, sticky=tk.W)
        self.companion_button = ttk.Button(
            title_row,
            text="连接浏览器",
            style="Link.TButton",
            command=self._start_browser_companion_setup,
        )
        self.companion_button.grid(row=0, column=1, sticky=tk.E, padx=(0, 14))
        self.advanced_button = ttk.Button(title_row, text="高级选项", style="Link.TButton", command=self._toggle_advanced)
        self.advanced_button.grid(row=0, column=2, sticky=tk.E)

        self.url_entry = ttk.Entry(source, textvariable=self.url_var)
        self.url_entry.grid(row=2, column=0, sticky=tk.EW, pady=(12, 0), padx=(0, 8))
        self.url_entry.bind("<Return>", lambda _event: self._start_analyze())
        self.url_entry.bind("<Escape>", lambda _event: self._clear_url())
        self.paste_button = ttk.Button(source, text="粘贴", width=5, command=self._paste_url)
        self.paste_button.grid(row=2, column=1, pady=(12, 0), padx=(0, 8))
        self.clear_button = ttk.Button(source, text="清空", width=5, style="Compact.TButton", command=self._clear_url)
        self.clear_button.grid(row=2, column=2, pady=(12, 0), padx=(0, 8))
        self.analyze_button = ttk.Button(source, text="解析媒体", style="Primary.TButton", command=self._start_analyze)
        self.analyze_button.grid(row=2, column=3, pady=(12, 0))

        self.notice_frame = tk.Frame(source, bg="#E9F2FF", highlightthickness=0)
        self.notice_frame.grid(row=3, column=0, columnspan=4, sticky=tk.EW, pady=(8, 0))
        self.notice_frame.columnconfigure(1, weight=1)
        self.notice_title = tk.Label(self.notice_frame, text="", bg="#E9F2FF", fg="#1559A6", font=("Microsoft YaHei UI", 9, "bold"), anchor=tk.W)
        self.notice_title.grid(row=0, column=0, sticky=tk.W, padx=(12, 8), pady=6)
        self.notice_text = tk.Label(self.notice_frame, text="", bg="#E9F2FF", fg="#3E628D", font=("Microsoft YaHei UI", 9), anchor=tk.W, justify=tk.LEFT, wraplength=900)
        self.notice_text.grid(row=0, column=1, sticky=tk.EW, padx=(0, 12), pady=6)
        self.notice_text.configure(width=1)
        self.notice_frame.bind("<Configure>", lambda event: self.notice_text.configure(
            wraplength=max(180, event.width - self.notice_title.winfo_reqwidth() - 40)
        ))
        self.notice_frame.grid_remove()

        self.advanced_window = tk.Toplevel(self)
        self.advanced_window.withdraw()
        self.advanced_window.title("高级下载设置")
        self.advanced_window.transient(self)
        self.advanced_window.resizable(False, False)
        self.advanced_window.protocol("WM_DELETE_WINDOW", self._toggle_advanced)
        self.advanced_window.bind("<Escape>", lambda _event: self._toggle_advanced())
        self.advanced_frame = ttk.Frame(self.advanced_window, style="Surface.TFrame", padding=18)
        self.advanced_frame.pack(fill=tk.BOTH, expand=True)
        self.advanced_frame.columnconfigure(1, weight=1)
        ttk.Label(self.advanced_frame, text="Referer", style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        self.referer_entry = ttk.Entry(self.advanced_frame, textvariable=self.referer_var, width=30)
        self.referer_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 16))
        ttk.Label(self.advanced_frame, text="并发", style="Muted.TLabel").grid(row=0, column=2, sticky=tk.W, padx=(0, 8))
        self.concurrency_entry = ttk.Spinbox(self.advanced_frame, from_=1, to=32, textvariable=self.concurrency_var, width=7)
        self.concurrency_entry.grid(row=0, column=3, sticky=tk.W, padx=(0, 16))
        self.keep_cache_check = ttk.Checkbutton(self.advanced_frame, text="保留续传缓存", variable=self.keep_cache_var)
        self.keep_cache_check.grid(row=0, column=4, sticky=tk.W)
        ttk.Label(self.advanced_frame, text="网盘提取码", style="Muted.TLabel").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 8), pady=(10, 0)
        )
        self.access_code_entry = ttk.Entry(self.advanced_frame, textvariable=self.access_code_var, show="*", width=20)
        self.access_code_entry.grid(row=1, column=1, sticky=tk.W, padx=(0, 16), pady=(10, 0))
        self.access_code_entry.bind("<Return>", self._shortcut_analyze)
        ttk.Label(self.advanced_frame, text="仅本次有效", style="Muted.TLabel").grid(
            row=1, column=2, columnspan=2, sticky=tk.W, pady=(10, 0)
        )
        self.baidupan_button = ttk.Button(
            self.advanced_frame,
            text="连接百度网盘",
            command=self._start_baidupan_login,
        )
        self.baidupan_button.grid(row=1, column=4, sticky=tk.E, pady=(10, 0))
        self.advanced_done_button = ttk.Button(self.advanced_frame, text="完成", command=self._toggle_advanced)
        self.advanced_done_button.grid(row=2, column=4, sticky=tk.E, pady=(14, 0))
        self.reduce_motion_check = ttk.Checkbutton(
            self.advanced_frame, text="减少动效", variable=self.reduce_motion_var,
            command=self._on_motion_preference_changed,
        )
        self.reduce_motion_check.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(10, 0))
        self.advanced_error_var = tk.StringVar()
        ttk.Label(self.advanced_frame, textvariable=self.advanced_error_var, style="Muted.TLabel", foreground="#B4232A", wraplength=420).grid(
            row=2, column=0, columnspan=4, sticky=tk.W, pady=(14, 0)
        )

        workspace = ttk.Frame(tab, style="App.TFrame")
        workspace.grid(row=1, column=0, sticky=tk.NSEW, pady=(10, 0))
        workspace.columnconfigure(0, weight=1)
        workspace.columnconfigure(1, minsize=315)
        workspace.rowconfigure(0, weight=1)

        candidates_frame = ttk.Frame(workspace, style="Surface.TFrame", padding=14)
        candidates_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 14))
        candidates_frame.columnconfigure(0, weight=1)
        candidates_frame.rowconfigure(3, weight=1)

        heading = ttk.Frame(candidates_frame, style="Surface.TFrame")
        heading.grid(row=0, column=0, columnspan=2, sticky=tk.EW)
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, text="可下载媒体", style="Section.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(heading, textvariable=self.candidate_count_var, style="Count.TLabel").grid(row=0, column=1, sticky=tk.E, padx=(8, 14))
        self.best_button = ttk.Button(heading, text="推荐项", width=7, style="Link.TButton", command=self._select_best_candidate, state=tk.DISABLED)
        self.best_button.grid(row=0, column=2, sticky=tk.E)
        self.select_all_button = ttk.Button(heading, text="全选", width=4, style="Link.TButton", command=self._select_all_candidates, state=tk.DISABLED)
        self.select_all_button.grid(row=0, column=3, padx=(8, 0))
        self.deselect_button = ttk.Button(heading, text="取消选择", width=8, style="Link.TButton", command=self._deselect_candidates, state=tk.DISABLED)
        self.deselect_button.grid(row=0, column=4, padx=(8, 0))
        search_row = ttk.Frame(candidates_frame, style="Surface.TFrame")
        search_row.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(6, 0))
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="搜索", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.candidate_search = ttk.Entry(search_row, textvariable=self.candidate_query_var)
        self.candidate_search.grid(row=0, column=1, sticky=tk.EW)
        self.candidate_search.bind("<Escape>", lambda _event: self.candidate_query_var.set(""))
        self.candidate_sort = ttk.Combobox(search_row, textvariable=self.candidate_sort_var,
                                          values=("原始顺序", "标题升序", "标题降序"), state="readonly", width=10)
        self.candidate_sort.grid(row=0, column=2, padx=(8, 0))
        self.candidate_sort.bind("<<ComboboxSelected>>", lambda _event: self._apply_candidate_filter())
        ttk.Label(candidates_frame, textvariable=self.selection_var, style="Muted.TLabel").grid(row=2, column=0, sticky=tk.W, pady=(5, 6))

        columns = ("title", "quality", "format", "duration", "origin")
        self.candidate_tree = ttk.Treeview(candidates_frame, columns=columns, show="headings", selectmode="extended", height=8)
        headings = {
            "title": ("标题", 190),
            "quality": ("画质", 90),
            "format": ("格式", 70),
            "duration": ("时长", 75),
            "origin": ("来源", 115),
        }
        for key, (label, width) in headings.items():
            self.candidate_tree.heading(key, text=label)
            self.candidate_tree.column(key, width=width, minwidth=65, stretch=(key == "title"))
        tree_scroll = ttk.Scrollbar(candidates_frame, orient=tk.VERTICAL, command=self.candidate_tree.yview)
        self.candidate_tree.configure(yscrollcommand=tree_scroll.set)
        self.candidate_tree.grid(row=3, column=0, sticky=tk.NSEW)
        tree_scroll.grid(row=3, column=1, sticky=tk.NS)
        self.candidate_empty_label = ttk.Label(
            candidates_frame,
            text="暂无媒体",
            style="Body.TLabel",
            anchor=tk.CENTER,
            justify=tk.CENTER,
        )
        self.candidate_empty_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self.candidate_tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_selection())
        self.candidate_tree.bind("<Control-a>", self._select_all_candidates)
        self.candidate_tree.bind("<Escape>", self._deselect_candidates)

        action = ttk.Frame(workspace, style="Surface.TFrame", padding=8)
        action.grid(row=0, column=1, sticky=tk.NSEW)
        action.columnconfigure(0, weight=1)
        action_heading = ttk.Frame(action, style="Surface.TFrame")
        action_heading.grid(row=0, column=0, sticky=tk.EW)
        action_heading.columnconfigure(0, weight=1)
        ttk.Label(action_heading, text="下载设置", style="Section.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Button(action_heading, text="打开目录", style="Link.TButton", command=self._open_output_dir).grid(row=0, column=1, sticky=tk.E)

        output_row = ttk.Frame(action, style="Surface.TFrame")
        output_row.grid(row=1, column=0, sticky=tk.EW, pady=(4, 0))
        output_row.columnconfigure(1, weight=1)
        ttk.Label(output_row, text="目录", style="Muted.TLabel", width=5).grid(row=0, column=0, sticky=tk.W)
        self.output_dir_entry = ttk.Entry(output_row, textvariable=self.output_dir_var)
        self.output_dir_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 7))
        self.output_dir_button = ttk.Button(output_row, text="选择", command=self._choose_output_dir)
        self.output_dir_button.grid(row=0, column=2)

        file_row = ttk.Frame(action, style="Surface.TFrame")
        file_row.grid(row=2, column=0, sticky=tk.EW, pady=(3, 0))
        file_row.columnconfigure(1, weight=1)
        ttk.Label(file_row, text="文件", style="Muted.TLabel", width=5).grid(row=0, column=0, sticky=tk.W)
        self.file_name_entry = ttk.Entry(file_row, textvariable=self.file_name_var, state=tk.DISABLED)
        self.file_name_entry.grid(row=0, column=1, sticky=tk.EW)

        quality_row = ttk.Frame(action, style="Surface.TFrame")
        quality_row.grid(row=3, column=0, sticky=tk.EW, pady=(4, 0))
        quality_row.columnconfigure(1, weight=1)
        ttk.Label(quality_row, text="质量", style="Muted.TLabel", width=5).grid(row=0, column=0, sticky=tk.W)
        self.quality_combo = ttk.Combobox(
            quality_row,
            textvariable=self.quality_var,
            values=("最佳质量", "最高 1080p", "最高 720p", "较小文件"),
            state="readonly",
        )
        self.quality_combo.grid(row=0, column=1, sticky=tk.EW)

        subtitle_row = ttk.Frame(action, style="Surface.TFrame")
        subtitle_row.grid(row=4, column=0, sticky=tk.EW, pady=(4, 0))
        subtitle_row.columnconfigure(1, weight=1)
        ttk.Label(subtitle_row, text="字幕", style="Muted.TLabel", width=5).grid(row=0, column=0, sticky=tk.W)
        self.subtitle_combo = ttk.Combobox(subtitle_row, textvariable=self.subtitle_var, values=("不下载字幕",), state="readonly")
        self.subtitle_combo.grid(row=0, column=1, sticky=tk.EW, padx=(0, 6))
        self.subtitle_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_subtitle_selected())
        self.subtitle_format_combo = ttk.Combobox(
            subtitle_row,
            textvariable=self.subtitle_format_var,
            values=("srt", "vtt", "ass"),
            state="readonly",
            width=5,
        )
        self.subtitle_format_combo.grid(row=0, column=2)

        subtitle_options = ttk.Frame(action, style="Surface.TFrame")
        subtitle_options.grid(row=5, column=0, sticky=tk.EW, pady=(2, 0))
        self.auto_subtitle_check = ttk.Checkbutton(subtitle_options, text="包含自动字幕", variable=self.auto_subtitle_var)
        self.auto_subtitle_check.pack(side=tk.LEFT)
        self.embed_subtitle_check = ttk.Checkbutton(subtitle_options, text="嵌入视频", variable=self.embed_subtitle_var)
        self.embed_subtitle_check.pack(side=tk.RIGHT)
        if not ffmpeg_capability().available:
            self.embed_subtitle_check.configure(state=tk.DISABLED)

        ttk.Separator(action).grid(row=6, column=0, sticky=tk.EW, pady=4)

        self.start_button = ttk.Button(action, text="开始下载", style="Primary.TButton", command=self._start_download, state=tk.DISABLED)
        self.start_button.grid(row=7, column=0, sticky=tk.EW)
        controls = ttk.Frame(action, style="Surface.TFrame")
        controls.grid(row=8, column=0, sticky=tk.EW, pady=(4, 0))
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

        activity = ttk.Notebook(tab, height=96)
        activity.grid(row=2, column=0, sticky=tk.NSEW, pady=(10, 0))
        progress_tab = ttk.Frame(activity, style="Surface.TFrame", padding=8)
        log_tab = ttk.Frame(activity, style="Surface.TFrame", padding=12)
        format_tab = ttk.Frame(activity, style="Surface.TFrame", padding=12)
        activity.add(progress_tab, text="任务进度")
        activity.add(format_tab, text="格式详情")
        activity.add(log_tab, text="活动日志")

        progress_tab.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_tab, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, columnspan=2, sticky=tk.EW)
        progress_actions = ttk.Frame(progress_tab, style="Surface.TFrame")
        progress_actions.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(4, 0))
        progress_actions.columnconfigure(0, weight=1)
        self.progress_detail_label = ttk.Label(progress_actions, textvariable=self.progress_detail_var,
                                               style="Muted.TLabel", width=1, wraplength=700)
        self.progress_detail_label.grid(row=0, column=0, sticky=tk.EW)
        self.retry_queue_button = ttk.Button(progress_tab, text="重试未完成", style="Compact.TButton",
                                             command=self._retry_unfinished_queue, state=tk.DISABLED)
        self.retry_queue_button.grid(row=2, column=1, padx=(8, 0))
        progress_actions.bind("<Configure>", lambda event: self.progress_detail_label.configure(
            wraplength=max(180, event.width - 20)))
        self.segment_canvas = tk.Canvas(progress_tab, height=34, bg="#FFFFFF", highlightthickness=0)
        self.segment_canvas.grid(row=2, column=0, sticky=tk.EW, pady=(4, 0))
        self.segment_canvas.bind("<Configure>", lambda _event: self._redraw_segments())

        log_tab.columnconfigure(0, weight=1)
        log_tab.rowconfigure(0, weight=1)
        self.log_text = ScrolledText(log_tab, height=6, wrap=tk.WORD, borderwidth=0, font=("Cascadia Mono", 9))
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        self.log_text.configure(bg="#F7F8FA", fg="#30343B", insertbackground="#30343B", relief=tk.FLAT, padx=10, pady=9, state=tk.DISABLED)

        format_tab.columnconfigure(0, weight=1)
        format_tab.rowconfigure(0, weight=1)
        format_columns = ("id", "resolution", "fps", "range", "video", "audio", "bitrate", "size", "protocol")
        self.format_tree = ttk.Treeview(format_tab, columns=format_columns, show="headings", height=4)
        format_headings = {
            "id": ("ID", 55),
            "resolution": ("分辨率", 90),
            "fps": ("帧率", 55),
            "range": ("动态范围", 75),
            "video": ("视频编码", 115),
            "audio": ("音频编码", 105),
            "bitrate": ("码率", 75),
            "size": ("大小", 75),
            "protocol": ("协议", 90),
        }
        for key, (label, width) in format_headings.items():
            self.format_tree.heading(key, text=label)
            self.format_tree.column(key, width=width, minwidth=45, stretch=key in {"video", "audio"})
        format_scroll = ttk.Scrollbar(format_tab, orient=tk.VERTICAL, command=self.format_tree.yview)
        self.format_tree.configure(yscrollcommand=format_scroll.set)
        self.format_tree.grid(row=0, column=0, sticky=tk.NSEW)
        format_scroll.grid(row=0, column=1, sticky=tk.NS)
        self._input_controls = (
            self.url_entry, self.paste_button, self.clear_button, self.referer_entry,
            self.access_code_entry, self.concurrency_entry, self.keep_cache_check,
            self.output_dir_entry, self.output_dir_button,
        )

    def _build_history_tab(self) -> None:
        tab = self.history_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(tab, style="Surface.TFrame", padding=16)
        toolbar.grid(row=0, column=0, sticky=tk.EW)
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, text="本地任务记录", style="Section.TLabel").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(toolbar, text="仅保存脱敏来源与本机输出信息", style="Muted.TLabel").grid(row=1, column=0, sticky=tk.W, pady=(3, 0))

        self.history_search = ttk.Entry(toolbar, width=24)
        self.history_search.insert(0, "搜索任务或来源")
        self.history_search.configure(foreground="#98A2B3")
        self.history_search.bind("<FocusIn>", self._clear_history_placeholder)
        self.history_search.bind("<FocusOut>", self._restore_history_placeholder)
        self.history_search.bind("<KeyRelease>", lambda _event: self._refresh_history())
        self.history_search.grid(row=0, column=1, padx=(12, 8))
        self.history_filter = ttk.Combobox(
            toolbar,
            textvariable=self.history_filter_var,
            values=("全部状态", "未完成", "排队中", "下载中", "已完成", "需重试", "已暂停", "已中断", "已停止"),
            state="readonly",
            width=9,
        )
        self.history_filter.bind("<<ComboboxSelected>>", lambda _event: self._refresh_history())
        self.history_filter.grid(row=0, column=2, padx=(0, 8))
        self.history_summary_label = ttk.Label(toolbar, textvariable=self.history_summary_var, style="Count.TLabel")
        self.history_summary_label.grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=(12, 0), pady=(3, 0))
        self.history_open_button = ttk.Button(toolbar, text="打开文件夹", command=self._open_history_output, state=tk.DISABLED)
        self.history_open_button.grid(row=0, column=3, rowspan=2, padx=(8, 0))
        self.history_retry_button = ttk.Button(
            toolbar,
            text="继续下载",
            style="Primary.TButton",
            command=self._retry_history,
            state=tk.DISABLED,
        )
        self.history_retry_button.grid(row=0, column=4, rowspan=2, padx=(8, 0))
        ttk.Button(toolbar, text="清除已完成", command=self._clear_completed_history).grid(row=0, column=5, rowspan=2, padx=(8, 0))

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
        self.history_tree.bind("<Return>", lambda _event: self._retry_history())
        self.history_tree.bind("<<TreeviewSelect>>", lambda _event: self._sync_history_actions())
        self.history_detail_label = ttk.Label(list_frame, textvariable=self.history_detail_var, style="Muted.TLabel", width=1, wraplength=900)
        self.history_detail_label.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=(10, 0))
        list_frame.bind("<Configure>", lambda event: self.history_detail_label.configure(wraplength=max(200, event.width - 32)))
        for status, color in {
            "downloading": "#1677FF",
            "completed": "#18794E",
            "failed": "#B4232A",
            "paused": "#8A5A00",
            "interrupted": "#8A5A00",
            "stopped": "#667085",
        }.items():
            self.history_tree.tag_configure(status, foreground=color)

    def _toggle_advanced(self) -> None:
        self._set_advanced_visible(not self.advanced_visible)

    def _set_advanced_visible(self, visible: bool) -> None:
        self.advanced_visible = visible
        if visible:
            self.advanced_window.geometry(f"+{max(0, self.winfo_rootx() + 40)}+{max(0, self.winfo_rooty() + 100)}")
            self.advanced_window.deiconify()
            self.advanced_window.lift()
        else:
            self.advanced_window.withdraw()
        self.advanced_button.configure(text="关闭高级选项" if visible else "高级选项")

    def _on_source_changed(self, *_args) -> None:
        """Invalidate candidates and source-specific credentials when the source changes."""

        url = self.url_var.get().strip()
        if url == self._input_url:
            return
        if self._input_url:
            self.access_code_var.set("")
            self.referer_var.set("")
        self._input_url = url
        self.advanced_error_var.set("")
        self.pending_history_retry = None
        self.history_retry_record_id = ""
        self.analyzed_url = ""
        if self.is_analyzing or self.is_downloading:
            return
        self._clear_candidates()
        self._queue_entries = []
        self._queue_source = ""
        self._sync_queue_retry()
        self.status_var.set("待解析新链接" if url else "准备就绪")
        self.selection_var.set("尚未解析")
        self.progress_detail_var.set("尚未开始任务")
        self._set_progress_value(0, animate=False)
        self._draw_segments(0)
        self._hide_notice()

    def _sync_input_controls(self) -> None:
        busy = self.is_analyzing or self.is_downloading
        self.candidate_search.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.candidate_sort.configure(state=tk.DISABLED if busy else "readonly")
        self._sync_queue_retry()
        self.candidate_tree.configure(selectmode="none" if busy else "extended")
        for control in self._input_controls:
            control.configure(state=tk.DISABLED if busy else tk.NORMAL)
        for control in (self.best_button, self.select_all_button, self.deselect_button):
            control.configure(state=tk.NORMAL if self.candidates and not busy else tk.DISABLED)
        self.analyze_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.file_name_entry.configure(state=tk.DISABLED if busy else tk.NORMAL)
        if busy:
            self.start_button.configure(state=tk.DISABLED)
            for control in (self.quality_combo, self.subtitle_combo, self.subtitle_format_combo, self.auto_subtitle_check, self.embed_subtitle_check):
                control.configure(state=tk.DISABLED)
        if not busy:
            self._sync_selection()

    def _paste_url(self) -> None:
        if self.is_analyzing or self.is_downloading:
            return
        try:
            value = self.clipboard_get().strip()
        except tk.TclError:
            value = ""
        if value:
            self.url_var.set(value)
            self.url_entry.focus_set()

    def _clear_url(self, _event=None) -> str:
        """Reset a completed input flow and cancel any pending history continuation. @codex-comment"""

        if self.is_analyzing or self.is_downloading:
            return "break"
        self.pending_history_retry = None
        self.history_retry_record_id = ""
        self.url_var.set("")
        self.access_code_var.set("")
        self._clear_candidates()
        self.selection_var.set("输入链接并解析后，这里会显示可下载媒体")
        self._hide_notice()
        self.status_var.set("准备就绪")
        self.url_entry.focus_set()
        return "break"

    def _focus_url(self, _event=None) -> str:
        self.main_notebook.select(self.download_tab)
        self.url_entry.focus_set()
        self.url_entry.selection_range(0, tk.END)
        return "break"

    def _shortcut_analyze(self, _event=None) -> str:
        if not self.is_downloading and not self.is_analyzing:
            self._start_analyze()
        return "break"

    def _clear_history_placeholder(self, _event=None) -> None:
        if self.history_search.get() == "搜索任务或来源":
            self.history_search.delete(0, tk.END)
            self.history_search.configure(foreground="#17191C")

    def _restore_history_placeholder(self, _event=None) -> None:
        if not self.history_search.get().strip():
            self.history_search.insert(0, "搜索任务或来源")
            self.history_search.configure(foreground="#98A2B3")

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
        """Validate input and retain query-based share codes only for the active queue. @codex-comment"""

        if self.is_analyzing or self.is_downloading:
            return
        url = self.url_var.get().strip()
        try:
            parsed = urlparse(url)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            valid = False
        if not valid:
            self._show_notice("warning", "链接格式不正确", "请输入完整的 http 或 https 视频页面、媒体直链或播放列表地址。")
            self.url_entry.focus_set()
            return

        access_code = self.access_code_var.get().strip()
        if not access_code:
            access_code = _share_access_code_from_url(url)
            if access_code:
                self.access_code_var.set(access_code)

        self._hide_notice()
        self._analysis_url = url
        self.queue_stop_event.clear()
        self._progress_paused = False
        self.advanced_error_var.set("")
        self._set_advanced_visible(False)
        self._set_busy_analyzing(True)
        self._clear_candidates()
        self._set_progress_value(0, animate=False)
        self._set_progress_busy(True)
        self.progress_detail_var.set("正在检查页面、媒体地址和通用解析器")
        self._draw_segments(0)
        self._log("开始解析媒体：" + redact_url(url))
        threading.Thread(
            target=self._analyze_worker,
            args=(url, self.referer_var.get().strip(), access_code),
            daemon=True,
        ).start()

    def _analyze_worker(self, url: str, referer: str, access_code: str) -> None:
        try:
            candidates = discover_candidates(
                url,
                referer=referer,
                callback=self._core_callback,
                access_code=access_code,
            )
            self.event_buffer.put("analysis_done", {"candidates": candidates})
        except Exception as exc:
            self.event_buffer.put("analysis_error", {"error": exc})

    def _start_download(self, *, retry_unfinished: bool = False) -> None:
        """Deduplicate selected media, resolve existing-output policy, and start the serial queue. @codex-comment"""

        if self.is_analyzing or self.is_downloading:
            return
        if not self.analyzed_url or self.analyzed_url != self.url_var.get().strip():
            self._show_notice("warning", "请重新解析链接", "当前链接已改变，原来的媒体列表不再适用。")
            self.url_entry.focus_set()
            return
        if self.__dict__.get("_filter_after_id") is not None:
            self._apply_candidate_filter()
        retry_entries = self._unfinished_queue_entries() if retry_unfinished else []
        selected_candidates = [item[0] for item in retry_entries] if retry_unfinished else self._selected_candidates()
        if not selected_candidates:
            self.history_retry_record_id = ""
            self._show_notice("warning", "尚未选择媒体", "先解析链接，然后选择一个或多个媒体条目。")
            return
        candidates, repeated_selection_count = _deduplicate_candidates(selected_candidates)
        concurrency = self._validated_concurrency()
        if concurrency is None:
            return

        if not retry_unfinished and not self.output_dir_var.get().strip():
            self._show_notice("warning", "请选择保存目录", "保存目录不能为空。")
            self.output_dir_entry.focus_set()
            return
        output_dir = Path(self.output_dir_var.get()).expanduser()
        try:
            if not retry_unfinished:
                output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.history_retry_record_id = ""
            self._show_error(classify_error(exc))
            return
        queue = [(candidate, path) for candidate, path, _record_id in retry_entries] if retry_unfinished else _plan_output_paths(
            candidates,
            output_dir,
            self.file_name_var.get(),
            avoid_existing=False,
        )
        queue, skipped_existing_count = _filter_duplicate_queue(queue, self.history_records)

        if not queue:
            self.history_retry_record_id = ""
            total_skipped = repeated_selection_count + skipped_existing_count
            self._show_notice("info", "没有需要下载的项目", f"已跳过 {total_skipped} 个重复或已存在的视频。")
            self._log(f"重复检测已跳过 {total_skipped} 个条目")
            return
        try:
            self._persist_queue(queue)
        except OSError as exc:
            self._show_error(classify_error(exc))
            return
        if len(queue) == 1 and queue[0][0].source_type != "baidupan":
            self.file_name_var.set(queue[0][1].name)
        if repeated_selection_count or skipped_existing_count:
            total_skipped = repeated_selection_count + skipped_existing_count
            self._show_notice("info", "已跳过重复视频", f"已跳过 {total_skipped} 项，其余 {len(queue)} 项将继续下载。")
            self._log(f"重复检测已跳过 {total_skipped} 个条目")

        keep_cache = self.keep_cache_var.get()
        preferences = self._download_preferences()
        referer_override = self.referer_var.get().strip()
        share_source_url = self.url_var.get().strip()
        share_access_code = self.access_code_var.get().strip()

        self.current_candidate = None
        self.current_record_id = ""
        self.current_job = None
        self.queue_total = len(queue)
        self.queue_completed = 0
        self.queue_failed = 0
        self.queue_stop_event.clear()
        self._progress_paused = False
        self._reset_progress_estimator()
        self._set_downloading_state(True)
        self._draw_segments(0)
        self._set_progress_value(0, animate=False)
        self._set_progress_busy(True)
        self.status_var.set("正在准备下载")
        self.progress_detail_var.set(f"正在建立下载队列，共 {len(queue)} 项")
        self._log(f"准备下载 {len(queue)} 个媒体条目")

        self.download_thread = threading.Thread(
            target=self._download_worker,
            args=(
                queue,
                concurrency,
                keep_cache,
                preferences,
                referer_override,
                share_source_url,
                share_access_code,
            ),
            daemon=True,
        )
        self.download_thread.start()

    def _persist_queue(self, queue: list[tuple[VideoCandidate, Path]]) -> None:
        """Save every planned item before launching any worker, preserving retry IDs and paths."""
        reusable = {(_candidate_media_key(candidate), str(path)): record_id
                    for candidate, path, record_id in self._queue_entries}
        records = []
        entries = []
        queued_at = time.time()
        for candidate, path in queue:
            key = (_candidate_media_key(candidate), str(path))
            record_id = reusable.get(key) or (self.history_retry_record_id if len(queue) == 1 else "") or uuid.uuid4().hex
            records.append(replace(self._make_history_record(candidate, path, record_id, "queued"), updated_at=queued_at))
            entries.append((candidate, path, record_id))
        self.history_records = self.history_store.upsert_many(records)
        self._queue_entries = entries
        self._queue_source = self.url_var.get().strip()
        self._queue_requires_code = bool(self.access_code_var.get().strip())
        self._refresh_history()

    def _unfinished_queue_entries(self) -> list[tuple[VideoCandidate, Path, str]]:
        if self._queue_source != self.url_var.get().strip():
            return []
        statuses = {record.record_id: record.status for record in self.history_records}
        return [entry for entry in self._queue_entries if statuses.get(entry[2]) in HISTORY_RETRY_STATES
                and (entry[0].source_type == "baidupan" or not entry[1].exists())]

    def _sync_queue_retry(self) -> None:
        entries = self._unfinished_queue_entries()
        enabled = bool(entries) and not self.is_downloading and not self.is_analyzing
        self.retry_queue_button.configure(state=tk.NORMAL if enabled else tk.DISABLED,
                                          text=f"重试未完成 ({len(entries)})" if entries else "重试未完成")

    def _retry_unfinished_queue(self) -> None:
        if self.is_downloading or self.is_analyzing or not self._unfinished_queue_entries():
            return
        if self._queue_requires_code and not self.access_code_var.get().strip():
            self._show_notice("warning", "需要提取码", "提取码已清除，重新输入后点击重试未完成。")
            self._set_advanced_visible(True)
            self.access_code_entry.focus_set()
            return
        self._start_download(retry_unfinished=True)

    def _validated_concurrency(self) -> int | None:
        try:
            value = int(self.concurrency_var.get())
            if 1 <= value <= 32:
                self.advanced_error_var.set("")
                return value
        except (ValueError, TypeError, tk.TclError):
            pass
        self._show_notice("warning", "并发数不正确", "请输入 1 到 32 之间的整数。")
        self.advanced_error_var.set("并发数：请输入 1 到 32 之间的整数。")
        self._set_advanced_visible(True)
        self.concurrency_entry.focus_set()
        self.concurrency_entry.selection_range(0, tk.END)
        return None

    def _download_worker(
        self,
        queue: list[tuple[VideoCandidate, Path]],
        concurrency: int,
        keep_cache: bool,
        preferences: DownloadPreferences,
        referer_override: str,
        share_source_url: str = "",
        share_access_code: str = "",
        pikpak_source_url: str = "",
        pikpak_access_code: str = "",
        overwrite_existing: bool = False,
    ) -> None:
        """Run selected media serially, retrying retryable items before advancing. @codex-comment"""

        share_source_url = share_source_url or pikpak_source_url
        share_access_code = share_access_code or pikpak_access_code
        completed = 0
        failed = 0
        stopped = False
        for index, (candidate, output_path) in enumerate(queue, start=1):
            if self.queue_stop_event.is_set():
                stopped = True
                break
            self.event_buffer.put(
                "queue_item_started",
                {"candidate": candidate, "output": str(output_path), "index": index, "total": len(queue)},
            )
            terminal: dict[str, object] = {}

            def queue_callback(event: str, payload: dict) -> None:
                if event in {"completed", "failed", "fatal", "stopped"}:
                    terminal.update({"event": event, "payload": payload})
                else:
                    self._core_callback(event, payload)

            referer = referer_override or candidate.referer or candidate.source_url
            headers = make_headers(referer)
            for item_attempt in range(1, BATCH_ITEM_MAX_ATTEMPTS + 1):
                terminal.clear()
                try:
                    if candidate.source_type == "baidupan":
                        job = BaiduPanDownloadJob(
                            candidate.source_url,
                            output_path,
                            access_code=share_access_code,
                            callback=queue_callback,
                        )
                    elif candidate.source_type in {"youtube", "ytdlp"}:
                        job = YouTubeDownloadJob(
                            candidate.url,
                            output_path,
                            concurrency=concurrency,
                            referer=referer,
                            callback=queue_callback,
                            preferences=preferences,
                            overwrite_existing=overwrite_existing,
                        )
                    elif candidate.source_type in {"direct", "pikpak"}:
                        url_refresher = None
                        if candidate.source_type == "pikpak" and candidate.media_id:

                            def refresh_url(current_candidate: VideoCandidate = candidate) -> str:
                                """Refresh one temporary PikPak URL using process-memory-only credentials. @codex-comment"""

                                refreshed = refresh_pikpak_candidate(
                                    current_candidate,
                                    share_source_url or current_candidate.source_url,
                                    access_code=share_access_code,
                                    callback=queue_callback,
                                )
                                if refreshed.source_type != "pikpak" or refreshed.media_id != current_candidate.media_id:
                                    raise HlsError("媒体类型或文件标识已变化，请重新解析分享后继续下载。")
                                return refreshed.url

                            url_refresher = refresh_url
                        job = DirectDownloadJob(
                            candidate.url,
                            output_path,
                            headers=headers,
                            callback=queue_callback,
                            url_refresher=url_refresher,
                            resume_key=candidate.media_id or _candidate_media_key(candidate),
                        )
                    else:
                        playlist = load_best_media_playlist(candidate.url, headers=headers)
                        job = DownloadJob(
                            playlist=playlist,
                            output_path=output_path,
                            headers=headers,
                            concurrency=concurrency,
                            keep_cache=keep_cache,
                            callback=queue_callback,
                            resume_key=_candidate_media_key(candidate),
                        )
                    self.current_job = job
                    if self.queue_stop_event.is_set():
                        job.stop()
                        terminal.update({"event": "stopped", "payload": {}})
                        break
                    self.event_buffer.put("job_ready", {"job": job})
                    job.run()
                except Exception as exc:
                    terminal.update({"event": "fatal", "payload": {"error": exc}})

                terminal_event = str(terminal.get("event") or "fatal")
                terminal_payload = terminal.get("payload") if isinstance(terminal.get("payload"), dict) else {}
                if terminal_event in {"completed", "stopped"}:
                    break

                error = terminal_payload.get("error") or terminal_payload.get("message") or "媒体下载未完成"
                if self.queue_stop_event.is_set():
                    terminal.update({"event": "stopped", "payload": {}})
                    break
                if item_attempt >= BATCH_ITEM_MAX_ATTEMPTS or not classify_error(error).retryable:
                    break

                next_attempt = item_attempt + 1
                self.event_buffer.put(
                    "queue_item_retry",
                    {
                        "error": error,
                        "index": index,
                        "total": len(queue),
                        "attempt": next_attempt,
                        "max_attempts": BATCH_ITEM_MAX_ATTEMPTS,
                    },
                )
                delay = min(5.0, BATCH_ITEM_RETRY_BACKOFF_SECONDS * item_attempt)
                if self.queue_stop_event.wait(delay):
                    terminal.update({"event": "stopped", "payload": {}})
                    break

            terminal_event = str(terminal.get("event") or "fatal")
            terminal_payload = terminal.get("payload") if isinstance(terminal.get("payload"), dict) else {}
            if terminal_event == "completed":
                completed += 1
                output = str(terminal_payload.get("output") or output_path)
                self.event_buffer.put("queue_item_completed", {"output": output, "index": index, "total": len(queue)})
            elif terminal_event == "stopped":
                stopped = True
                self.event_buffer.put("queue_item_stopped", {"index": index, "total": len(queue)})
                break
            else:
                failed += 1
                error = terminal_payload.get("error") or terminal_payload.get("message") or "媒体下载未完成"
                self.event_buffer.put("queue_item_failed", {"error": error, "index": index, "total": len(queue)})

        self.event_buffer.put(
            "queue_finished",
            {
                "completed": completed,
                "failed": failed,
                "total": len(queue),
                "stopped": stopped,
            },
        )

    def _toggle_pause(self) -> None:
        if not self.current_job:
            return
        if self.current_job.pause_event.is_set():
            self.current_job.pause()
        else:
            self.current_job.resume()

    def _stop_download(self) -> None:
        self.queue_stop_event.set()
        self._set_progress_value(self.current_progress_value, animate=False)
        self.pause_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.DISABLED)
        self.partial_button.configure(state=tk.DISABLED)
        self.status_var.set("正在安全停止")
        if self.current_job:
            self.current_job.stop()

    def _combine_partial(self) -> None:
        job = self.current_job
        if not self.is_downloading or self.queue_stop_event.is_set() or not isinstance(job, DownloadJob) or self.partial_export_job is not None:
            return
        self.partial_export_job = job
        self._sync_partial_button()
        threading.Thread(target=self._partial_export_worker, args=(job,), daemon=True).start()

    def _partial_export_worker(self, job: DownloadJob) -> None:
        try:
            output = job.combine(require_all=False)
            self.event_buffer.put("partial_export_completed", {"job": job, "output": str(output)})
        except Exception as exc:
            self.event_buffer.put("partial_export_failed", {"job": job, "error": exc})

    def _sync_partial_button(self) -> None:
        supported = self.is_downloading and isinstance(self.current_job, DownloadJob) and not self.queue_stop_event.is_set()
        self.partial_button.configure(
            state=tk.NORMAL if supported and self.partial_export_job is None else tk.DISABLED,
            text="正在导出" if self.partial_export_job is not None else "合并部分",
        )

    def _selected_candidate(self) -> VideoCandidate | None:
        candidates = self._selected_candidates()
        return candidates[0] if candidates else None

    def _selected_candidates(self) -> list[VideoCandidate]:
        selection = set(self.candidate_tree.selection())
        if not selection:
            return []
        indices: list[int] = []
        for item in self.candidate_tree.get_children():
            if item not in selection:
                continue
            try:
                index = int(item)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(self.candidates):
                indices.append(index)
        return [self.candidates[index] for index in indices]

    def _schedule_candidate_filter(self, *_args) -> None:
        if self._filter_after_id is not None:
            self.after_cancel(self._filter_after_id)
        self._filter_after_id = self.after(150, self._apply_candidate_filter)

    def _apply_candidate_filter(self) -> None:
        if self._filter_after_id is not None:
            self.after_cancel(self._filter_after_id)
            self._filter_after_id = None
        if self.is_analyzing or self.is_downloading:
            return
        words = self.candidate_query_var.get().casefold().split()
        indices = [i for i, candidate in enumerate(self.candidates)
                   if all(word in f"{candidate.title} {_candidate_origin_label(candidate)}".casefold() for word in words)]
        order = self.candidate_sort_var.get()
        if order != "原始顺序":
            indices.sort(key=lambda i: _natural_title_key(self.candidates[i].title), reverse=order == "标题降序")
        visible = tuple(str(i) for i in indices)
        visible_set = set(visible)
        selection = tuple(i for i in self.candidate_tree.selection() if i in visible_set)
        self.candidate_tree.selection_set(selection)
        self.candidate_tree.set_children("", *visible)
        self.candidate_count_var.set(f"{len(visible)} / {len(self.candidates)} 项")
        if visible:
            self.candidate_empty_label.place_forget()
        elif self.candidates:
            self.candidate_empty_label.configure(text="没有匹配的媒体")
            self.candidate_empty_label.place(relx=0.5, rely=0.78, anchor=tk.CENTER)
        self._sync_selection()
        self.select_all_button.configure(state=tk.NORMAL if visible else tk.DISABLED)
        self.best_button.configure(state=tk.NORMAL if visible else tk.DISABLED)

    def _select_best_candidate(self) -> None:
        if not self.candidates or self.is_analyzing or self.is_downloading:
            return
        items = self.candidate_tree.get_children()
        if not items:
            return
        if self.candidates[0].playlist_count > 1:
            self.candidate_tree.selection_set(items)
            self.candidate_tree.focus(items[0])
            self._sync_selection()
            return
        iid = max(items, key=lambda item: candidate_score(self.candidates[int(item)]))
        self.candidate_tree.selection_set(iid)
        self.candidate_tree.focus(iid)
        self.candidate_tree.see(iid)
        self._sync_selection()

    def _select_all_candidates(self, _event=None) -> str:
        if self.candidates and not self.is_analyzing and not self.is_downloading:
            self.candidate_tree.selection_set(self.candidate_tree.get_children())
            self._sync_selection()
        return "break"

    def _deselect_candidates(self, _event=None) -> str:
        if not self.is_analyzing and not self.is_downloading:
            self.candidate_tree.selection_remove(self.candidate_tree.selection())
            self._sync_selection()
        return "break"

    def _sync_selection(self) -> None:
        """Reflect candidate type and batch shape in output controls and task details. @codex-comment"""

        if self.is_analyzing or self.is_downloading:
            return
        selection = tuple(self.candidate_tree.selection())
        selection_changed = selection != self._selection_ids
        self._selection_ids = selection
        candidates = self._selected_candidates()
        self.deselect_button.configure(state=tk.NORMAL if candidates else tk.DISABLED)
        self.start_button.configure(state=tk.NORMAL if candidates and self.analyzed_url == self.url_var.get().strip() else tk.DISABLED)
        if not candidates:
            self.start_button.configure(text="开始下载", state=tk.DISABLED)
            self.file_name_var.set("未选择媒体")
            self.file_name_entry.configure(state=tk.DISABLED)
            self.selection_var.set(f"已选择 0 / {len(self.candidates)} 项" if self.candidates else "尚未解析")
            self._refresh_format_details([])
            self.partial_button.grid_remove()
            self.task_controls.columnconfigure(2, weight=0)
            return
        candidate = candidates[0]
        file_stem = sanitize_file_name(candidate.title.split(" / ", 1)[0], "video")
        editable_name = len(candidates) == 1 and candidate.source_type != "baidupan"
        self.file_name_entry.configure(state=tk.NORMAL if editable_name else tk.DISABLED)
        if selection_changed:
            if len(candidates) > 1:
                self.file_name_var.set("按各条目标题命名")
            elif candidate.source_type == "baidupan":
                self.file_name_var.set("由分享目录决定")
            else:
                self.file_name_var.set(file_stem + _default_suffix_for_candidate(candidate))
        if len(candidates) > 1:
            self.selection_var.set(f"已选择 {len(candidates)} / {len(self.candidates)} 项 · 按列表顺序依次下载")
            self.start_button.configure(text=f"下载选中项 ({len(candidates)})")
        else:
            self.selection_var.set(_candidate_summary(candidate))
            self.start_button.configure(text="开始下载")
        if candidate.referer and not self.referer_var.get().strip():
            self.referer_var.set(candidate.referer)
        self._refresh_format_details(candidates)
        if candidate.source_type == "hls":
            self.task_controls.columnconfigure(2, weight=1)
            self.partial_button.grid()
        else:
            self.partial_button.grid_remove()
            self.task_controls.columnconfigure(2, weight=0)

    def _refresh_format_details(self, candidates: list[VideoCandidate]) -> None:
        for item in self.format_tree.get_children():
            self.format_tree.delete(item)
        for index, media_format in enumerate(candidates[0].formats if candidates else ()):
            self.format_tree.insert(
                "",
                tk.END,
                iid=f"format-{index}",
                values=(
                    media_format.format_id or "-",
                    media_format.resolution or "仅音频",
                    f"{media_format.fps:g}" if media_format.fps else "-",
                    media_format.dynamic_range or "SDR",
                    media_format.vcodec or "-",
                    media_format.acodec or "-",
                    f"{media_format.tbr:.0f} kbps" if media_format.tbr else "-",
                    _format_size(media_format.filesize) if media_format.filesize else "-",
                    media_format.protocol or "-",
                ),
            )

        self.subtitle_choices = _subtitle_choice_map(candidates)
        subtitle_values = ["不下载字幕", "全部可用", *self.subtitle_choices] if self.subtitle_choices else ["不下载字幕"]
        self.subtitle_combo.configure(values=subtitle_values)
        if self.subtitle_var.get() not in subtitle_values:
            self.subtitle_var.set("不下载字幕")
        subtitle_state = tk.NORMAL if self.subtitle_choices else tk.DISABLED
        self.auto_subtitle_check.configure(state=subtitle_state)
        self.subtitle_combo.configure(state="readonly" if self.subtitle_choices else tk.DISABLED)
        self.subtitle_format_combo.configure(state="readonly" if self.subtitle_choices else tk.DISABLED)
        self.embed_subtitle_check.configure(state=tk.NORMAL if self.subtitle_choices and ffmpeg_capability().available else tk.DISABLED)
        self.quality_combo.configure(state="readonly" if any(item.source_type in {"youtube", "ytdlp"} for item in candidates) else tk.DISABLED)

    def _on_subtitle_selected(self) -> None:
        _language, automatic_only = self.subtitle_choices.get(self.subtitle_var.get(), ("", False))
        if automatic_only:
            self.auto_subtitle_var.set(True)

    def _download_preferences(self) -> DownloadPreferences:
        quality = {
            "最佳质量": "best",
            "最高 1080p": "1080p",
            "最高 720p": "720p",
            "较小文件": "compact",
        }.get(self.quality_var.get(), "best")
        subtitle = self.subtitle_var.get()
        languages: tuple[str, ...] = ()
        if subtitle == "全部可用":
            languages = ("all",)
        elif subtitle and subtitle != "不下载字幕":
            language, automatic_only = self.subtitle_choices.get(subtitle, (subtitle, False))
            languages = (language,)
            if automatic_only:
                self.auto_subtitle_var.set(True)
        return DownloadPreferences(
            quality=quality,
            subtitle_languages=languages,
            include_auto_subtitles=self.auto_subtitle_var.get(),
            subtitle_format=self.subtitle_format_var.get() or "srt",
            embed_subtitles=self.embed_subtitle_var.get() and ffmpeg_capability().available,
        )

    def _clear_candidates(self) -> None:
        if self._filter_after_id is not None:
            self.after_cancel(self._filter_after_id)
            self._filter_after_id = None
        for item in self._candidate_ids:
            if self.candidate_tree.exists(item):
                self.candidate_tree.delete(item)
        self._candidate_ids = ()
        self.candidate_query_var.set("")
        self.candidate_sort_var.set("原始顺序")
        self.candidates = []
        self.analyzed_url = ""
        self._selection_ids = ()
        self.candidate_count_var.set("等待解析")
        self.selection_var.set("正在查找可下载媒体")
        if hasattr(self, "candidate_empty_label"):
            self.candidate_empty_label.configure(text="正在解析媒体" if self.is_analyzing else "暂无媒体")
            self.candidate_empty_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        for item in self.candidate_tree.get_children():
            self.candidate_tree.delete(item)
        if hasattr(self, "format_tree"):
            for item in self.format_tree.get_children():
                self.format_tree.delete(item)
        self.subtitle_combo.configure(values=("不下载字幕",))
        self.subtitle_choices = {}
        self.subtitle_var.set("不下载字幕")
        self.start_button.configure(text="开始下载")
        self.best_button.configure(state=tk.DISABLED)
        self.select_all_button.configure(state=tk.DISABLED)
        self.deselect_button.configure(state=tk.DISABLED)
        self.file_name_entry.configure(state=tk.DISABLED)
        self.start_button.configure(state=tk.DISABLED)

    def _set_busy_analyzing(self, busy: bool) -> None:
        """Reflect analysis state in primary and history actions. @codex-comment"""

        self.is_analyzing = busy
        self.candidate_empty_label.configure(text="正在解析媒体" if busy else "暂无媒体")
        self.analyze_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.status_var.set("正在解析媒体" if busy else "等待开始下载")
        self._sync_input_controls()
        self._sync_history_actions()

    def _poll_browser_inbox(self) -> None:
        try:
            if not self.is_downloading and not self.is_analyzing:
                candidate = self.browser_inbox.pop()
                if candidate:
                    self.main_notebook.select(self.download_tab)
                    self.url_var.set(candidate.url)
                    self.referer_var.set(candidate.source_page)
                    self.status_var.set("已收到浏览器媒体")
                    self._log(f"浏览器伴侣已发送 {candidate.kind.upper()} 媒体：{redact_url(candidate.url)}")
                    self._show_notice("info", "已收到浏览器媒体", "正在使用当前页面作为 Referer 并自动解析媒体。")
                    self.after(80, self._start_analyze)
        except BrowserCompanionError as exc:
            self._log(f"浏览器伴侣收件箱已重置：{exc}", "warning")
            try:
                self.browser_inbox.clear()
            except BrowserCompanionError:
                pass
        finally:
            self.after(BROWSER_INBOX_INTERVAL_MS, self._poll_browser_inbox)

    def _start_browser_companion_setup(self) -> None:
        installer, bridge, extension = _browser_companion_package_paths()
        if not installer.is_file() or not bridge.is_file() or not extension.is_dir():
            self._show_notice("warning", "浏览器组件不完整", "请使用正式便携包中的桌面程序启动此功能。")
            return
        approved = messagebox.askyesno(
            "连接浏览器",
            "将为当前 Windows 用户注册本地浏览器桥接，并打开扩展目录。是否继续？",
            parent=self,
        )
        if not approved:
            return
        self.companion_button.configure(state=tk.DISABLED)
        self.status_var.set("正在注册浏览器伴侣")
        threading.Thread(
            target=self._browser_companion_setup_worker,
            args=(installer, _browser_companion_installed_extension_path()),
            daemon=True,
        ).start()

    def _start_baidupan_login(self) -> None:
        """Open the isolated connector login console without reading account credentials. @codex-comment"""

        connector = find_baidupcs_executable()
        if connector is None:
            self._show_notice("warning", "百度网盘连接器缺失", "请使用包含 BaiduPCS-Go 的完整 Windows 便携包。")
            return
        approved = messagebox.askyesno(
            "连接百度网盘",
            "将打开独立的百度网盘连接器终端。账号输入和授权状态由连接器管理，本软件不会读取输入内容。是否继续？",
            parent=self,
        )
        if not approved:
            return
        try:
            subprocess.Popen(
                [str(connector), "login"],
                cwd=connector.parent,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except OSError as exc:
            self._show_error(classify_error(HlsError("百度网盘连接器无法启动")))
            self._log(f"百度网盘连接器启动失败：{exc}", "warning")
            return
        self.status_var.set("等待百度网盘登录")
        self._show_notice("info", "已打开百度网盘连接器", "在独立终端完成登录后，返回客户端重新开始下载。")

    def _browser_companion_setup_worker(self, installer: Path, extension: Path) -> None:
        try:
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(installer),
                ],
                cwd=installer.parent,
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "注册脚本执行失败").strip().splitlines()[-1]
                raise OSError(detail)
            self.event_buffer.put("browser_companion_installed", {"extension": str(extension)})
        except (OSError, subprocess.SubprocessError) as exc:
            self.event_buffer.put("browser_companion_install_error", {"error": str(exc)})

    def _set_downloading_state(self, active: bool) -> None:
        """Enable only controls supported by the current provider job. @codex-comment"""

        self.is_downloading = active
        self.start_button.configure(state=tk.DISABLED if active else (tk.NORMAL if self.candidates else tk.DISABLED))
        self.analyze_button.configure(state=tk.DISABLED if active else tk.NORMAL)
        pause_supported = (
            active
            and self.current_candidate is not None
            and self.current_candidate.source_type not in {"baidupan", "ytdlp", "youtube"}
        )
        self.pause_button.configure(state=tk.NORMAL if pause_supported else tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL if active else tk.DISABLED)
        self._sync_partial_button()
        if not active:
            self.pause_button.configure(text="暂停")
            self._set_progress_value(self.current_progress_value, animate=False)
        self._sync_input_controls()
        self._sync_history_actions()

    def _core_callback(self, event: str, payload: dict) -> None:
        self.event_buffer.put(event, payload)

    def _drain_events(self) -> None:
        for event, payload in self.event_buffer.drain():
            if event != "segment":
                self._flush_segment_updates()
            self._handle_event(event, payload)
        self._flush_segment_updates()
        self.after(UI_REFRESH_INTERVAL_MS, self._drain_events)

    def _handle_event(self, event: str, payload: dict) -> None:
        """Apply coalesced worker events and clear in-memory share codes at terminal states. @codex-comment"""

        if event == "analysis_done":
            self._on_analysis_done(payload["candidates"])
        elif event == "analysis_error":
            if self._discard_stale_analysis():
                return
            error = classify_error(payload.get("error", "解析失败"))
            retry_record = self.pending_history_retry
            self.pending_history_retry = None
            self.access_code_var.set("")
            self._set_progress_value(0, animate=False)
            self._set_busy_analyzing(False)
            self.candidate_count_var.set("解析未完成")
            self.progress_detail_var.set("解析未完成")
            if retry_record is not None:
                self.status_var.set("历史任务无法继续")
                self._log(f"历史任务重新解析失败：{error.detail or error.message}", "warning")
                if error.code in {"pikpak_code_required", "pikpak_code_invalid", "baidupan_code_required", "baidupan_code_invalid"}:
                    self.pending_history_retry = retry_record
                    self._show_notice("warning", "需要重新输入提取码", "输入提取码后按回车，匹配成功后会自动继续下载。")
                else:
                    self._show_notice("warning", "需要更新原链接", "脱敏来源已失效，请粘贴原视频页面链接后重新解析。")
            else:
                self._show_error(error)
            if error.code in {"pikpak_code_required", "pikpak_code_invalid", "baidupan_code_required", "baidupan_code_invalid"}:
                self.advanced_error_var.set(error.message)
                self._set_advanced_visible(True)
                self.after_idle(self.access_code_entry.focus_set)
        elif event == "browser_companion_installed":
            self.companion_button.configure(state=tk.NORMAL)
            extension = Path(str(payload.get("extension", "")))
            try:
                if extension.is_dir():
                    os.startfile(extension)
                _open_browser_extensions_page()
            except OSError as exc:
                self._log(f"浏览器扩展目录未能自动打开：{exc}", "warning")
            self.status_var.set("浏览器伴侣已注册")
            self._show_notice("success", "浏览器伴侣已注册", "扩展目录和浏览器扩展管理页已打开。")
        elif event == "browser_companion_install_error":
            self.companion_button.configure(state=tk.NORMAL)
            self.status_var.set("浏览器伴侣注册失败")
            self._show_notice("warning", "浏览器伴侣注册失败", str(payload.get("error", "请检查便携包是否完整。")))
        elif event == "job_ready":
            if payload["job"] is self.current_job:
                self._set_downloading_state(True)
        elif event == "queue_item_started":
            self._progress_paused = False
            candidate = payload["candidate"]
            output_path = Path(payload["output"])
            self.current_candidate = candidate
            index = int(payload.get("index", 1))
            entry = self._queue_entries[index - 1] if 0 < index <= len(self._queue_entries) else None
            self.current_record_id = (entry[2] if entry and entry[1] == output_path else "") or self.history_retry_record_id or uuid.uuid4().hex
            self.history_retry_record_id = ""
            self._reset_progress_estimator()
            self._set_progress_value(0, animate=False)
            self._set_progress_busy(True)
            self._draw_segments(0)
            self.pause_button.configure(text="暂停", state=tk.DISABLED)
            self.partial_button.configure(state=tk.DISABLED)
            self._create_history_record(candidate, output_path)
            total = int(payload.get("total", 1))
            self.status_var.set(f"正在下载 {index}/{total}")
            self.progress_detail_var.set(f"队列 {index}/{total} · 正在准备 {output_path.name}")
            self._log(f"队列 {index}/{total}：{redact_url(candidate.url)}")
        elif event == "queue_item_completed":
            self.queue_completed += 1
            self._set_progress_value(100, animate=False)
            output = Path(str(payload.get("output", "")))
            self._history_update(status="completed", progress=100.0, output_path=output, force=True)
            self._log("已完成：" + output.name)
        elif event == "queue_item_retry":
            self._set_progress_busy(True)
            attempt = int(payload.get("attempt", 2))
            max_attempts = int(payload.get("max_attempts", BATCH_ITEM_MAX_ATTEMPTS))
            index = int(payload.get("index", 1))
            total = int(payload.get("total", 1))
            error = classify_error(payload.get("error", "媒体下载未完成"))
            self.status_var.set(f"正在重试当前文件 {attempt}/{max_attempts}")
            self.progress_detail_var.set(f"队列 {index}/{total} · 当前文件第 {attempt}/{max_attempts} 次尝试")
            self._log(f"当前文件上次尝试失败：{error.message}；开始第 {attempt}/{max_attempts} 次尝试", "warning")
        elif event == "queue_item_failed":
            self._set_progress_value(self.current_progress_value, animate=False)
            self.queue_failed += 1
            error = classify_error(payload.get("error", "媒体下载未完成"))
            self._history_update(status="failed", error=error, force=True)
            self._log(f"下载失败：{error.message}", "error")
        elif event == "queue_item_stopped":
            self._history_update(status="stopped", force=True)
            self._log("当前条目已停止，剩余队列不再启动。")
        elif event == "queue_finished":
            self.history_retry_record_id = ""
            self.access_code_var.set("")
            self._set_downloading_state(False)
            self.current_job = None
            queued_ids = {entry[2] for entry in self._queue_entries}
            pending = [replace(record, status="stopped") for record in self.history_records
                       if record.record_id in queued_ids and record.status == "queued"]
            if pending:
                self.history_records = self.history_store.upsert_many(pending)
                self._refresh_history()
            self._sync_queue_retry()
            completed = int(payload.get("completed", 0))
            failed = int(payload.get("failed", 0))
            total = int(payload.get("total", 0))
            if payload.get("stopped"):
                self.status_var.set("下载队列已停止")
                self.progress_detail_var.set(f"已停止：完成 {completed} 项，失败 {failed} 项，未完成 {max(0, total - completed - failed)} 项")
                self._show_notice("info", "队列已停止", f"已完成 {completed}/{total} 项，已下载数据和续传缓存均已保留。")
            elif failed:
                self.status_var.set("下载队列已完成，部分项目需重试")
                self.progress_detail_var.set(f"已处理 {total} 项：完整下载 {completed} 项，失败 {failed} 项")
                self._show_notice("warning", "队列已完成", f"成功 {completed} 项，失败 {failed} 项；可在任务记录中选择失败项继续下载。")
            else:
                self.status_var.set("下载队列已完成")
                self.progress_detail_var.set(f"已完成 {completed}/{total} 项")
                self._show_notice("success", "下载完成", f"队列中的 {completed} 个下载任务已完整写入保存目录。")
        elif event == "started":
            self._draw_segments(int(payload.get("total", 0)))
            self._history_update(status="downloading", force=True)
            self._log(f"已载入续传缓存，共 {payload.get('total', 0)} 个任务单元")
        elif event == "segment":
            self._update_segment(int(payload["index"]), str(payload["status"]))
        elif event == "progress":
            self._update_progress(payload)
        elif event == "paused":
            self._progress_paused = True
            self._paused_progress_busy = self._progress_busy
            self._set_progress_value(self.current_progress_value, animate=False)
            self.pause_button.configure(text="继续")
            self.status_var.set("已暂停")
            self._history_update(status="paused", force=True)
        elif event == "resumed":
            self._progress_paused = False
            if self.__dict__.get("_paused_progress_busy"):
                self._set_progress_busy(True)
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
        elif event in {"partial_export_completed", "partial_export_failed"}:
            if payload.get("job") is not self.partial_export_job:
                return
            self.partial_export_job = None
            self._sync_partial_button()
            if event == "partial_export_completed":
                name = Path(payload["output"]).name
                self._log("部分媒体已导出：" + name)
                self._show_notice("info", "部分媒体已导出", name)
            else:
                error = classify_error(payload.get("error", "部分媒体导出失败"))
                self._log(f"部分媒体导出失败：{error.detail or error.message}", "warning")
                self._show_notice("warning", "部分媒体暂未导出", "下载队列未受影响，可等待更多分片完成后重试；详情见活动日志。")
        elif event == "combining" and not payload.get("partial"):
            self._set_progress_busy(True)
            self.status_var.set("正在封装媒体文件")
            self._log("开始生成输出文件")
        elif event == "combined" and not payload.get("partial"):
            self._log("输出文件已生成：" + Path(payload.get("output", "")).name)
        elif event == "completed":
            self._set_downloading_state(False)
            self._set_progress_value(100, animate=False)
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

    def _discard_stale_analysis(self) -> bool:
        if self._analysis_url == self.url_var.get().strip():
            return False
        self._set_progress_value(0, animate=False)
        self._clear_candidates()
        self._set_busy_analyzing(False)
        self.pending_history_retry = None
        self.status_var.set("待解析新链接")
        self._show_notice("info", "链接已改变", "已忽略上一个链接的解析结果，请解析当前链接。")
        return True

    def _on_analysis_done(self, candidates: list[VideoCandidate]) -> None:
        """Render discovered media or auto-continue one pending history retry. @codex-comment"""

        if self._discard_stale_analysis():
            return
        self._set_progress_value(0, animate=False)
        self.progress_detail_var.set("解析完成，等待开始下载")
        self._set_busy_analyzing(False)
        self._clear_candidates()
        self.analyzed_url = self._analysis_url
        self.advanced_error_var.set("")
        self.candidates = candidates
        self._candidate_ids = tuple(str(i) for i in range(len(candidates)))
        self.candidate_count_var.set(f"共 {len(candidates)} 项")
        if not candidates:
            retry_record = self.pending_history_retry
            self.pending_history_retry = None
            self.candidate_count_var.set("未找到可下载版本")
            self.selection_var.set("没有可下载版本。可以补充 Referer，或从浏览器伴侣重新发送当前媒体。")
            self.best_button.configure(state=tk.DISABLED)
            self.start_button.configure(state=tk.DISABLED)
            if retry_record is not None:
                self._show_notice("warning", "未找到原任务媒体", "请粘贴最新的原视频页面链接，再重新解析并选择该视频。")
            else:
                self._show_notice("warning", "没有找到可下载媒体", "请确认页面仍可访问；受保护页面可以尝试连接浏览器后重新解析。")
            return
        self.candidate_empty_label.place_forget()
        for index, candidate in enumerate(candidates):
            self.candidate_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    candidate.title.split(" / ", 1)[0],
                    candidate.resolution or "自动",
                    _candidate_format_label(candidate),
                    _format_duration(candidate.duration),
                    _candidate_origin_label(candidate),
                ),
            )
        is_playlist = bool(candidates and candidates[0].playlist_count > 1)
        if is_playlist:
            self.best_button.grid_remove()
        else:
            self.best_button.grid()
        self._sync_input_controls()
        if self.pending_history_retry is not None:
            self._continue_history_after_analysis()
            return
        if is_playlist:
            self.candidate_tree.selection_set("0")
            self.candidate_tree.focus("0")
            self._sync_selection()
            self._log(f"发现播放列表中的 {len(candidates)} 个媒体条目，默认选择第一项。")
            self._show_notice("success", "播放列表解析完成", f"找到 {len(candidates)} 个条目。")
        else:
            self._select_best_candidate()
            self._log(f"发现 {len(candidates)} 个可下载媒体，已选择推荐项。")
            self._show_notice("success", "解析完成", f"找到 {len(candidates)} 个媒体版本，已按画质与码率排序。")

    def _cancel_progress_motion(self) -> None:
        if self._motion_after_id is not None:
            self.after_cancel(self._motion_after_id)
            self._motion_after_id = None

    def _set_progress_busy(self, busy: bool) -> None:
        self._cancel_progress_motion()
        self.progress.stop()
        self._progress_busy = busy
        moving = busy and not self.reduce_motion_var.get() and not self._progress_paused and not self.queue_stop_event.is_set()
        self.progress.configure(mode="indeterminate" if moving else "determinate",
                                value=0 if busy else self._progress_target)
        if moving:
            self.progress.start(35)

    def _set_progress_value(self, value: float, *, animate: bool = True) -> None:
        self._cancel_progress_motion()
        if self._progress_busy:
            self._set_progress_busy(False)
        self._progress_target = min(100.0, max(0.0, value))
        shown = float(self.progress["value"])
        if not animate or self.reduce_motion_var.get() or self._progress_paused or self.queue_stop_event.is_set() or shown >= self._progress_target:
            self.progress["value"] = self._progress_target
            return
        self._animate_progress()

    def _animate_progress(self) -> None:
        self._motion_after_id = None
        shown = float(self.progress["value"])
        remaining = self._progress_target - shown
        self.progress["value"] = self._progress_target if remaining < 0.1 else shown + remaining * 0.35
        if remaining >= 0.1:
            self._motion_after_id = self.after(16, self._animate_progress)

    def _on_motion_preference_changed(self) -> None:
        if self._progress_busy:
            self._set_progress_busy(True)
        else:
            self._set_progress_value(self._progress_target, animate=False)

    def _reset_progress_estimator(self) -> None:
        self._set_progress_value(0, animate=False)
        self.last_progress_bytes = 0
        self.last_progress_done = 0
        self.last_progress_time = time.monotonic()
        self.smoothed_speed = 0.0
        self.smoothed_unit_rate = 0.0
        self.current_progress_value = 0.0
        self.current_bytes_done = 0

    def _update_progress(self, payload: dict) -> None:
        total = max(0, int(payload.get("total", 0)))
        done = max(0, int(payload.get("done", 0)))
        failed = max(0, int(payload.get("failed", 0)))
        downloading = max(0, int(payload.get("downloading", 0)))
        bytes_done = max(0, int(payload.get("bytes_done", 0)))
        percent = min(100.0, done / total * 100) if total else 0.0
        self.current_progress_value = percent
        self.current_bytes_done = max(self.current_bytes_done, bytes_done)
        if total:
            self._set_progress_value(percent)
        elif not self._progress_busy:
            self._set_progress_busy(True)

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
        parts = [f"{percent:.0f}%", f"{done}/{total}", _format_size(bytes_done)] if total else ["大小未知", _format_size(bytes_done)]
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
        self._dirty_segment_blocks = set()
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
        if self.segment_total <= 0 or index < 0 or index >= self.segment_total:
            return
        self.segment_status[index] = status
        block = min(self.segment_block_count - 1, ((index + 1) * self.segment_block_count - 1) // self.segment_total)
        self._dirty_segment_blocks.add(block)

    def _flush_segment_updates(self) -> None:
        """Repaint each changed block once per contiguous event batch, not once per segment."""

        for block in self._dirty_segment_blocks:
            item = self.segment_items.get(block)
            if item:
                self.segment_canvas.itemconfigure(item, fill=_status_color(self._block_status(block)))
        self._dirty_segment_blocks.clear()

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
        """Create or restart local history without persisting signed URLs or access credentials. @codex-comment"""

        record = self._make_history_record(candidate, output_path, self.current_record_id)
        self.history_records = self.history_store.upsert(record)
        self._refresh_history()

    def _make_history_record(self, candidate: VideoCandidate, output_path: Path,
                             record_id: str, status: str = "preparing") -> DownloadRecord:
        source_url = _history_source_url(candidate)
        host = urlparse(source_url).hostname or ""
        previous = next((item for item in self.history_records if item.record_id == record_id), None)
        return DownloadRecord(
            record_id=record_id,
            title=sanitize_file_name(candidate.title.split(" / ", 1)[0], "video"),
            source_type=candidate.source_type,
            source_url=source_url,
            source_host=host,
            output_path=str(output_path),
            status=status,
            media_key=_candidate_media_key(candidate),
            progress=previous.progress if previous else 0.0,
            bytes_done=previous.bytes_done if previous else 0,
            updated_at=time.time(),
        )

    def _history_update(
        self,
        status: str | None = None,
        progress: float | None = None,
        bytes_done: int | None = None,
        error: UserFacingError | None = None,
        force: bool = False,
        output_path: Path | None = None,
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
        completed_bytes = None
        if status == "completed" and output_path is not None:
            try:
                if output_path.is_file():
                    completed_bytes = output_path.stat().st_size
            except OSError:
                pass
        updated = replace(
            current,
            status=status or current.status,
            progress=max(current.progress, self.current_progress_value),
            bytes_done=completed_bytes if completed_bytes is not None else max(current.bytes_done, self.current_bytes_done),
            output_path=str(output_path) if output_path is not None else current.output_path,
            updated_at=now,
            error_code=error.code if error else ("" if status == "completed" else current.error_code),
            error_message=error.message if error else ("" if status == "completed" else current.error_message),
        )
        self.history_records = self.history_store.upsert(updated)
        self.last_history_write = now
        self._refresh_history()

    def _refresh_history(self) -> None:
        """Reconcile changed rows while retaining selection, focus, and scroll position. @codex-comment"""

        if not hasattr(self, "history_tree"):
            return
        existing = self.history_tree.get_children()
        previous_rows = getattr(self, "_history_rows", {})
        query = self.history_search.get().strip() if hasattr(self, "history_search") else ""
        if query == "搜索任务或来源":
            query = ""
        visible_records = [
            record for record in self.history_records if _history_record_matches(record, query, self.history_filter_var.get())
        ]
        self.history_summary_var.set(f"{len(visible_records)} / {len(self.history_records)} 个任务")
        visible_ids = tuple(record.record_id for record in visible_records)
        visible_set = set(visible_ids)
        scroll_position = self.history_tree.yview()[0]
        for item in existing:
            if item not in visible_set:
                self.history_tree.delete(item)
        rows = {}
        existing_set = set(existing)
        for record in visible_records:
            values = (
                record.title,
                _history_type_label(record.source_type),
                _history_status_label(record.status),
                f"{record.progress:.0f}%",
                _format_size(record.bytes_done),
                time.strftime("%Y-%m-%d %H:%M", time.localtime(record.updated_at)),
                record.output_path,
            )
            rows[record.record_id] = (record.status, values)
            if record.record_id not in existing_set:
                self.history_tree.insert("", tk.END, iid=record.record_id, tags=(record.status,), values=values)
            elif previous_rows.get(record.record_id) != rows[record.record_id]:
                self.history_tree.item(record.record_id, tags=(record.status,), values=values)
        if existing != visible_ids:
            self.history_tree.set_children("", *visible_ids)
            self.history_tree.yview_moveto(scroll_position)
        self._history_rows = rows
        self._sync_history_actions()

    def _selected_history(self) -> DownloadRecord | None:
        selection = self.history_tree.selection()
        if not selection:
            return None
        record_id = selection[0]
        return next((item for item in self.history_records if item.record_id == record_id), None)

    def _sync_history_actions(self) -> None:
        """Enable history continuation only for inactive retryable records. @codex-comment"""

        if not hasattr(self, "history_retry_button") or not hasattr(self, "history_tree"):
            return
        record = self._selected_history()
        enabled = bool(
            record
            and record.status in HISTORY_RETRY_STATES
            and not self.is_analyzing
            and not self.is_downloading
        )
        self.history_retry_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        if hasattr(self, "history_open_button"):
            self.history_open_button.configure(state=tk.NORMAL if record and record.output_path else tk.DISABLED)
        if hasattr(self, "history_detail_var"):
            self.history_detail_var.set(
                f"{_history_status_label(record.status)} · {record.error_message or record.title}"
                if record else "请选择任务查看状态"
            )

    def _open_history_output(self) -> None:
        record = self._selected_history()
        if not record or not record.output_path:
            self.history_detail_var.set("请选择有保存位置的任务")
            return
        path, _name = _history_output_settings(record)
        try:
            path = path.expanduser().resolve()
            while not path.is_dir() and path != path.parent:
                path = path.parent
            if not path.is_dir():
                self.history_detail_var.set("保存目录不存在或磁盘未连接，请确认文件位置后重试。")
                return
            os.startfile(path)
        except OSError as exc:
            self.history_detail_var.set(classify_error(exc).message)

    def _retry_history(self) -> None:
        """Re-analyze one retryable history source and continue into its original output path. @codex-comment"""

        record = self._selected_history()
        if not record:
            self._show_notice("info", "请选择任务", "选择一条需重试、已停止或已中断的任务后，点击“继续下载”。")
            return
        if self.is_analyzing or self.is_downloading:
            self._show_notice("info", "当前任务仍在运行", "当前解析或下载结束后，再继续历史任务。")
            return
        if record.status not in HISTORY_RETRY_STATES:
            self._show_notice("info", "该任务无需重试", "只有需重试、已停止或已中断的任务可以继续下载。")
            return
        if not record.output_path:
            self._show_notice("warning", "保存位置不可用", "这条旧记录缺少保存位置，请重新建立下载任务。")
            return

        self.url_var.set(record.source_url)
        output_dir, file_name = _history_output_settings(record)
        self.output_dir_var.set(str(output_dir))
        self.file_name_var.set(file_name)
        self.main_notebook.select(self.download_tab)
        if not _history_retry_source_available(record):
            self.pending_history_retry = None
            self._show_notice("warning", "需要更新原链接", "这条旧记录只保留了脱敏地址，请粘贴完整的原视频页面链接后重新解析。")
            self.url_entry.focus_set()
            return

        self.pending_history_retry = record
        self._log(f"继续历史任务：{record.title}")
        self._show_notice("info", "正在恢复历史任务", "正在重新确认媒体地址；匹配成功后会从本机续传缓存继续。")
        self._start_analyze()

    def _continue_history_after_analysis(self) -> None:
        """Match a pending history item, restore its output, and start exactly one retry queue. @codex-comment"""

        record = self.pending_history_retry
        self.pending_history_retry = None
        if record is None:
            return
        candidate_index = _history_retry_candidate_index(record, self.candidates)
        if candidate_index is None:
            self.status_var.set("未找到原任务媒体")
            self._log(f"历史任务未匹配到原媒体：{record.title}", "warning")
            self._show_notice("warning", "未找到原任务媒体", "解析结果与历史记录不一致，请粘贴最新原链接并手动选择对应视频。")
            return

        iid = str(candidate_index)
        self.candidate_tree.selection_set(iid)
        self.candidate_tree.focus(iid)
        self.candidate_tree.see(iid)
        self._sync_selection()
        output_dir, file_name = _history_output_settings(record)
        self.output_dir_var.set(str(output_dir))
        self.file_name_var.set(file_name)
        self.history_retry_record_id = record.record_id
        self._show_notice("info", "继续下载", f"正在从已有进度继续 {Path(record.output_path).name}。")
        self._start_download()

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
        message = redact_sensitive_text(message)
        follow_tail = self.log_text.yview()[1] >= 0.999
        prefix = {"error": "[错误]", "warning": "[警告]", "debug": "[调试]", "info": "[信息]"}.get(level, "[信息]")
        self.log_text.configure(state=tk.NORMAL)
        try:
            self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')} {prefix} {message}\n")
            excess = int(self.log_text.index("end-1c").split(".")[0]) - 1 - MAX_LOG_LINES
            if excess > 0:
                self.log_text.delete("1.0", f"{excess + 1}.0")
            if follow_tail:
                self.log_text.see(tk.END)
        finally:
            self.log_text.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        if self.is_downloading and not messagebox.askyesno("退出下载器", "当前任务仍在下载。退出后可依靠缓存继续，确定退出吗？"):
            return
        self.queue_stop_event.set()
        if self.current_job:
            self.current_job.stop()
        self.destroy()

    def destroy(self) -> None:
        self._cancel_progress_motion()
        self.progress.stop()
        if self._filter_after_id is not None:
            self.after_cancel(self._filter_after_id)
            self._filter_after_id = None
        super().destroy()


def _status_color(status: str) -> str:
    return {
        "pending": "#DDE2EA",
        "partial": "#82B4F8",
        "downloading": "#F3B33D",
        "done": "#2BA471",
        "error": "#E5484D",
    }.get(status, "#DDE2EA")


def _natural_title_key(title: str) -> tuple:
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r"(\d+)", title))


def _format_duration(seconds: float) -> str:
    if not seconds:
        return "-"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _share_access_code_from_url(url: str) -> str:
    """Extract a provider-scoped query code without persisting the source URL. @codex-comment"""

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in {"pan.baidu.com", "mypikpak.com", "mypikpak.net"}:
        return ""
    query = parse_qs(parsed.query, keep_blank_values=False)
    for key in ("pwd", "s_code"):
        value = str((query.get(key) or [""])[0]).strip()
        if value:
            return value[:128]
    return ""


def _format_size(value: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _format_eta(seconds: float) -> str:
    if 0 < seconds < 1:
        return "不到 1 秒"
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def _default_suffix_for_candidate(candidate: VideoCandidate) -> str:
    if candidate.source_type == "baidupan":
        return ""
    if candidate.source_type in {"youtube", "ytdlp"}:
        return ".mp4"
    if candidate.source_type in {"direct", "pikpak"}:
        if candidate.container:
            return "." + candidate.container.lstrip(".").lower()
        suffix = Path(candidate.url.split("?", 1)[0]).suffix.lower()
        return suffix if suffix else ".mp4"
    return ".ts"


def _candidate_kind_label(candidate: VideoCandidate) -> str:
    return {
        "youtube": "YouTube",
        "ytdlp": "网页媒体",
        "direct": "视频直链",
        "pikpak": "PikPak 分享",
        "baidupan": "百度网盘分享",
        "hls": "HLS",
    }.get(candidate.source_type, "媒体")


def _candidate_format_label(candidate: VideoCandidate) -> str:
    if candidate.source_type == "baidupan":
        return "目录"
    if candidate.container:
        return candidate.container.upper()
    if candidate.source_type in {"direct", "pikpak"}:
        return Path(urlparse(candidate.url).path).suffix.lstrip(".").upper() or "VIDEO"
    if candidate.source_type == "hls":
        return "M3U8"
    return "自动"


def _candidate_engine_label(candidate: VideoCandidate) -> str:
    if candidate.source_type == "baidupan":
        return "BaiduPCS-Go"
    if candidate.source_type in {"youtube", "ytdlp"}:
        return "yt-dlp"
    if candidate.source_type in {"direct", "pikpak"}:
        return "HTTP"
    return "HLS / AES" if candidate.encrypted else "HLS"


def _candidate_origin_label(candidate: VideoCandidate) -> str:
    if candidate.extractor:
        return candidate.extractor
    return urlparse(candidate.url).hostname or "未知来源"


def _candidate_structure_label(candidate: VideoCandidate) -> str:
    if candidate.source_type == "baidupan":
        return "完整分享目录"
    if candidate.source_type in {"youtube", "ytdlp"}:
        return "自动选择并合并"
    if candidate.source_type in {"direct", "pikpak"}:
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


def _subtitle_choice_map(candidates: list[VideoCandidate]) -> dict[str, tuple[str, bool]]:
    availability: dict[str, set[bool]] = {}
    for candidate in candidates:
        for track in candidate.subtitles:
            if track.language:
                availability.setdefault(track.language, set()).add(track.automatic)
    choices: dict[str, tuple[str, bool]] = {}
    for language in sorted(availability):
        automatic_only = availability[language] == {True}
        label = f"{language}（自动）" if automatic_only else language
        choices[label] = (language, automatic_only)
    return choices


def _history_status_label(status: str) -> str:
    return {
        "queued": "排队中",
        "preparing": "准备中",
        "downloading": "下载中",
        "paused": "已暂停",
        "completed": "已完成",
        "failed": "需重试",
        "stopped": "已停止",
        "interrupted": "已中断",
    }.get(status, "未知")


def _history_type_label(source_type: str) -> str:
    return {
        "youtube": "YouTube",
        "ytdlp": "网页",
        "direct": "直链",
        "pikpak": "PikPak",
        "baidupan": "百度网盘",
        "hls": "HLS",
    }.get(source_type, source_type.upper())


def _history_filter_status(label: str) -> str | None:
    return {
        "排队中": "queued",
        "下载中": "downloading",
        "已完成": "completed",
        "需重试": "failed",
        "已暂停": "paused",
        "已中断": "interrupted",
        "已停止": "stopped",
    }.get(label)


def _history_record_matches(record: DownloadRecord, query: str, filter_label: str) -> bool:
    if filter_label == "未完成" and record.status == "completed":
        return False
    expected_status = _history_filter_status(filter_label)
    if expected_status and record.status != expected_status:
        return False
    normalized_query = query.strip().casefold()
    if not normalized_query:
        return True
    searchable = " ".join(
        (
            record.title,
            record.source_host,
            record.source_type,
            record.output_path,
            _history_status_label(record.status),
        )
    ).casefold()
    return normalized_query in searchable


def _history_source_url(candidate: VideoCandidate) -> str:
    """Return a query-free retry source, using a canonical YouTube ID path when available. @codex-comment"""

    media_id = candidate.media_id.strip()
    safe_media_id = bool(media_id) and len(media_id) <= 128 and all(character.isalnum() or character in "_-" for character in media_id)
    if candidate.source_type == "youtube" and safe_media_id:
        return f"https://youtu.be/{media_id}"
    return redact_url(candidate.source_url or candidate.url)


def _history_retry_source_available(record: DownloadRecord) -> bool:
    """Reject legacy retry sources whose essential query identity was removed. @codex-comment"""

    parsed = urlparse(record.source_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if record.source_type == "youtube":
        if host == "youtu.be":
            return bool(parsed.path.strip("/"))
        if host.endswith("youtube.com") and parsed.path.rstrip("/") == "/watch":
            return bool(parse_qs(parsed.query).get("v"))
    return True


def _history_output_settings(record: DownloadRecord) -> tuple[Path, str]:
    """Map a history output to the directory and filename controls expected by its provider. @codex-comment"""

    output = Path(record.output_path).expanduser()
    if record.source_type == "baidupan":
        return output, "由分享目录决定"
    return output.parent, output.name


def _history_retry_candidate_index(record: DownloadRecord, candidates: list[VideoCandidate]) -> int | None:
    """Match exact media identity first, then one unambiguous title/type fallback. @codex-comment"""

    if record.media_key:
        exact = [index for index, candidate in enumerate(candidates) if _candidate_media_key(candidate) == record.media_key]
        if len(exact) == 1:
            return exact[0]
        return None

    title_matches = [
        index
        for index, candidate in enumerate(candidates)
        if candidate.source_type == record.source_type
        and sanitize_file_name(candidate.title.split(" / ", 1)[0], "video").casefold() == record.title.casefold()
    ]
    if len(title_matches) == 1:
        return title_matches[0]
    return None


def _candidate_media_key(candidate: VideoCandidate) -> str:
    """Hash a credential-free stable provider ID or media URL for duplicate detection. @codex-comment"""

    source_type = candidate.source_type.strip().lower() or "unknown"
    if candidate.media_id:
        identity = f"{source_type}|id|{candidate.media_id.strip()}"
    else:
        raw_url = candidate.source_url if source_type == "baidupan" else (candidate.url or candidate.source_url)
        normalized_url = media_identity_url(raw_url)
        identity = f"{source_type}|url|{normalized_url}"
        source_url = redact_url(candidate.source_url)
        if candidate.playlist_index and normalized_url == source_url:
            identity += f"|playlist-index|{candidate.playlist_index}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _deduplicate_candidates(candidates: list[VideoCandidate]) -> tuple[list[VideoCandidate], int]:
    """Keep the first occurrence of each stable media identity. @codex-comment"""

    unique: list[VideoCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        media_key = _candidate_media_key(candidate)
        if media_key in seen:
            continue
        seen.add(media_key)
        unique.append(candidate)
    return unique, len(candidates) - len(unique)


def _duplicate_queue_indices(
    queue: list[tuple[VideoCandidate, Path]],
    history_records: list[DownloadRecord],
) -> set[int]:
    """Find queue items backed by an existing target or an available completed-history output. @codex-comment"""

    completed_media = {
        record.media_key
        for record in history_records
        if record.status == "completed"
        and record.media_key
        and record.output_path
        and Path(record.output_path).expanduser().exists()
    }
    duplicates: set[int] = set()
    for index, (candidate, output_path) in enumerate(queue):
        target_exists = candidate.source_type != "baidupan" and output_path.exists()
        if target_exists or _candidate_media_key(candidate) in completed_media:
            duplicates.add(index)
    return duplicates


def _filter_duplicate_queue(
    queue: list[tuple[VideoCandidate, Path]],
    history_records: list[DownloadRecord],
) -> tuple[list[tuple[VideoCandidate, Path]], int]:
    """Remove existing or already completed media while preserving queue order. @codex-comment"""

    duplicate_indices = _duplicate_queue_indices(queue, history_records)
    return (
        [item for index, item in enumerate(queue) if index not in duplicate_indices],
        len(duplicate_indices),
    )


def _available_output_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}-{int(time.time())}{path.suffix}")


def _plan_output_paths(
    candidates: list[VideoCandidate],
    output_dir: Path,
    requested_file_name: str,
    avoid_existing: bool = True,
) -> list[tuple[VideoCandidate, Path]]:
    """Reserve unique file paths while mapping provider-managed directory jobs to their root. @codex-comment"""

    reserved: set[Path] = set()
    result: list[tuple[VideoCandidate, Path]] = []
    for candidate in candidates:
        if candidate.source_type == "baidupan":
            result.append((candidate, output_dir))
            continue
        if len(candidates) == 1:
            file_name = sanitize_file_name(requested_file_name, "video")
        else:
            file_name = sanitize_file_name(candidate.title.split(" / ", 1)[0], "video")
        if not Path(file_name).suffix:
            file_name += _default_suffix_for_candidate(candidate)
        path = output_dir / file_name
        if (avoid_existing and path.exists()) or path in reserved:
            for index in range(1, 1000):
                alternative = path.with_name(f"{path.stem} ({index}){path.suffix}")
                if (not avoid_existing or not alternative.exists()) and alternative not in reserved:
                    path = alternative
                    break
        reserved.add(path)
        result.append((candidate, path))
    return result


def _resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


def _browser_companion_package_paths(base_path: Path | None = None) -> tuple[Path, Path, Path]:
    root = base_path
    if root is None:
        root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return (
        root / "install_browser_companion.ps1",
        root / "UniversalVideoDownloaderBridge.exe",
        root / "browser-extension",
    )


def _browser_companion_installed_extension_path(local_app_data: Path | None = None) -> Path:
    root = local_app_data or Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return root / "UniversalVideoDownloader" / "browser-companion" / "extension"


def _open_browser_extensions_page() -> bool:
    roots = [Path(value) for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if (value := os.environ.get(name))]
    candidates = [root / relative for root in roots for relative in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe")]
    for executable in candidates:
        if executable.is_file():
            subprocess.Popen([str(executable), "chrome://extensions/"])
            return True
    return False


if __name__ == "__main__":
    try:
        app = UniversalVideoDownloaderApp()
        app.mainloop()
    except HlsError as exc:
        messagebox.showerror("错误", str(exc))
