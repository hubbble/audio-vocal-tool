#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
音訊處理工具 — 拖放式圖形介面 (GUI)

功能：
  - 把檔案 / 影片直接拖進視窗（或用「加入檔案」按鈕）
  - 操作：影片轉音檔 / 音訊轉檔 / 分割 / 人聲分離 / 去靜音 / pipeline / 合併
  - 獨立的進度區：目前檔案進度條 + 整體進度條
  - 可隨時停止處理

執行（用 venv 裡的 python，才有 torch / demucs）：
  .venv312\Scripts\python.exe gui.py

底層是呼叫 audio_tool.py，因此所有功能與 CLI 一致。
"""

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# 拖放支援（沒裝也能用按鈕加入檔案）
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND = True
except ImportError:
    _DND = False

HERE = Path(__file__).resolve().parent
AUDIO_TOOL = HERE / "audio_tool.py"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# ---------------------------------------------------------------- 風格定義
BG      = "#F5F5F7"   # 視窗背景（Apple 淺灰）
CARD    = "#FFFFFF"   # 卡片
BORDER  = "#E5E5EA"   # 卡片邊框
TEXT    = "#1D1D1F"   # 主文字
SUBTEXT = "#86868B"   # 次要文字
ACCENT  = "#0071E3"   # 蘋果藍
ACCENT_HOVER = "#147CE5"
TROUGH  = "#E9E9EB"   # 進度條底色
DANGER  = "#FF3B30"

FONT      = ("Segoe UI", 10)
FONT_SM   = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI Semibold", 10)
FONT_TITLE = ("Segoe UI Semibold", 16)
FONT_MONO = ("Consolas", 9)

# 操作定義：顯示名稱 -> (CLI 操作, 輸出型態 dir/file, 是否用到 GPU 裝置)
OPERATIONS = {
    "人聲分離 ＋ 去靜音":  ("pipeline", "dir", True),
    "人聲分離":            ("vocals",   "dir", True),
    "去除靜音":            ("trim",     "dir", False),
    "剪輯（自選片段移除）": ("cut",      "dir", False),
    "音量標準化":          ("normalize", "dir", False),
    "音訊轉檔":            ("convert",  "dir", False),
    "分割音檔":            ("split",    "dir", False),
    "影片轉音檔":          ("extract",  "dir", False),
    "合併成一個 MP3":      ("merge",    "file", False),
}

# 標準化預設：顯示名稱 -> 附加的 CLI 參數
NORM_PRESETS = {
    "-14 LUFS（串流平台）": ["--mode", "lufs", "--lufs", "-14"],
    "-16 LUFS（通用）":     ["--mode", "lufs", "--lufs", "-16"],
    "-23 LUFS（廣播）":     ["--mode", "lufs", "--lufs", "-23"],
    "峰值 -1 dBFS（快速）": ["--mode", "peak", "--peak-dbfs", "-1"],
}

SUPPORTED_OUT_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus")


def fmt_time(sec: float) -> str:
    """秒數 → '1:23.4' 或 '1:02:03.4'。"""
    m, s = divmod(max(0.0, sec), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{int(m)}:{s:04.1f}"


def parse_time(s: str) -> float:
    """'90' / '1:30' / '0:01:30.5' → 秒數；格式錯誤丟 ValueError。"""
    parts = [float(p) for p in s.strip().split(":")]
    if not 1 <= len(parts) <= 3:
        raise ValueError(s)
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec

FORMATS  = ["mp3", "wav", "flac", "m4a", "ogg", "opus"]
BITRATES = ["320k", "256k", "192k", "128k"]

_PCT_RE = re.compile(r"(\d{1,3})(?:\.\d+)?%")


class App:
    def __init__(self, root):
        self.root = root
        root.title("音訊處理工具")
        root.geometry("760x720")
        root.minsize(680, 620)
        root.configure(bg=BG)

        self.files = []
        self.queue = queue.Queue()
        self.proc = None
        self.running = False
        self.stop_flag = threading.Event()

        self._build_style()
        self._build_ui()
        self._on_op_change()
        self.root.after(100, self._drain_queue)

    # ------------------------------------------------------------- 樣式
    def _build_style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT, font=FONT,
                    borderwidth=0, focuscolor=BG)

        s.configure("Card.TFrame", background=CARD)
        s.configure("Bg.TLabel", background=BG, foreground=TEXT)
        s.configure("Title.TLabel", background=BG, foreground=TEXT,
                    font=FONT_TITLE)
        s.configure("Card.TLabel", background=CARD, foreground=TEXT)
        s.configure("CardBold.TLabel", background=CARD, foreground=TEXT,
                    font=FONT_BOLD)
        s.configure("Sub.TLabel", background=CARD, foreground=SUBTEXT,
                    font=FONT_SM)

        # 主要按鈕（蘋果藍）
        s.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                    font=FONT_BOLD, borderwidth=0, padding=(18, 8))
        s.map("Accent.TButton",
              background=[("disabled", "#B8D4F0"), ("active", ACCENT_HOVER)],
              foreground=[("disabled", "#FFFFFF")])

        # 次要按鈕（白底細框）
        s.configure("Ghost.TButton", background=CARD, foreground=TEXT,
                    borderwidth=1, relief="solid", padding=(12, 5))
        s.map("Ghost.TButton",
              background=[("active", "#F0F0F2"), ("disabled", CARD)],
              foreground=[("disabled", "#C7C7CC")],
              bordercolor=[("!disabled", BORDER), ("disabled", BORDER)])

        # 停止按鈕
        s.configure("Stop.TButton", background=CARD, foreground=DANGER,
                    borderwidth=1, relief="solid", padding=(14, 8),
                    font=FONT_BOLD)
        s.map("Stop.TButton",
              background=[("active", "#FFF0EF"), ("disabled", CARD)],
              foreground=[("disabled", "#C7C7CC")],
              bordercolor=[("!disabled", BORDER), ("disabled", BORDER)])

        # 進度條
        s.configure("Accent.Horizontal.TProgressbar", troughcolor=TROUGH,
                    background=ACCENT, borderwidth=0, thickness=6)

        # 下拉選單 / 輸入框
        s.configure("TCombobox", fieldbackground=CARD, background=CARD,
                    bordercolor=BORDER, arrowcolor=SUBTEXT, padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", CARD)])
        s.configure("TEntry", fieldbackground=CARD, bordercolor=BORDER,
                    padding=4)
        s.configure("TSpinbox", fieldbackground=CARD, bordercolor=BORDER,
                    arrowcolor=SUBTEXT, padding=4)

    def _card(self, parent, title=None, subtitle=None):
        """建立一張白色圓角感卡片，回傳內容 frame。"""
        outer = tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1, bd=0)
        outer.pack(fill="x", padx=16, pady=(0, 10))
        inner = ttk.Frame(outer, style="Card.TFrame")
        inner.pack(fill="both", expand=True, padx=14, pady=10)
        if title:
            head = ttk.Frame(inner, style="Card.TFrame")
            head.pack(fill="x")
            ttk.Label(head, text=title, style="CardBold.TLabel").pack(side="left")
            if subtitle:
                ttk.Label(head, text=subtitle, style="Sub.TLabel").pack(
                    side="left", padx=(8, 0))
        return outer, inner

    # ------------------------------------------------------------- UI
    def _build_ui(self):
        # --- 標題 ---
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=16, pady=(14, 10))
        ttk.Label(header, text="音訊處理工具", style="Title.TLabel").pack(side="left")

        # --- 檔案卡片 ---
        hint = "拖曳檔案到清單中" if _DND else "未安裝 tkinterdnd2，請用按鈕加入"
        outer, files_card = self._card(self.root, "輸入檔案", hint)
        outer.pack_configure(fill="both", expand=True)
        files_card.pack_configure(fill="both", expand=True)

        body = ttk.Frame(files_card, style="Card.TFrame")
        body.pack(fill="both", expand=True, pady=(8, 0))

        self.listbox = tk.Listbox(
            body, height=6, selectmode=tk.EXTENDED, activestyle="none",
            bg="#FAFAFC", fg=TEXT, font=FONT, bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            selectbackground=ACCENT, selectforeground="#FFFFFF")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, command=self.listbox.yview)
        sb.pack(side="left", fill="y")
        self.listbox.config(yscrollcommand=sb.set)

        if _DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self._on_drop)

        btns = ttk.Frame(files_card, style="Card.TFrame")
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="＋ 加入檔案", style="Ghost.TButton",
                   command=self._add_files).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="＋ 加入資料夾", style="Ghost.TButton",
                   command=self._add_folder).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="移除選取", style="Ghost.TButton",
                   command=self._remove_sel).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="清空", style="Ghost.TButton",
                   command=self._clear).pack(side="left")
        self.count_lbl = ttk.Label(btns, text="0 個檔案", style="Sub.TLabel")
        self.count_lbl.pack(side="right")

        # --- 設定卡片 ---
        _, opt_card = self._card(self.root, "設定")
        grid = ttk.Frame(opt_card, style="Card.TFrame")
        grid.pack(fill="x", pady=(8, 0))

        ttk.Label(grid, text="操作", style="Sub.TLabel").grid(
            row=0, column=0, sticky="w")
        self.op_var = tk.StringVar(value=list(OPERATIONS)[0])
        op_menu = ttk.Combobox(grid, textvariable=self.op_var, state="readonly",
                               values=list(OPERATIONS), width=22)
        op_menu.grid(row=1, column=0, sticky="w", padx=(0, 14))
        op_menu.bind("<<ComboboxSelected>>", lambda e: self._on_op_change())

        ttk.Label(grid, text="裝置", style="Sub.TLabel").grid(
            row=0, column=1, sticky="w")
        self.dev_var = tk.StringVar(value="auto")
        self.dev_menu = ttk.Combobox(grid, textvariable=self.dev_var,
                                     state="readonly",
                                     values=["auto", "cuda", "cpu"], width=7)
        self.dev_menu.grid(row=1, column=1, sticky="w", padx=(0, 14))

        # 轉檔專用參數
        self.fmt_lbl = ttk.Label(grid, text="輸出格式", style="Sub.TLabel")
        self.fmt_var = tk.StringVar(value="mp3")
        self.fmt_menu = ttk.Combobox(grid, textvariable=self.fmt_var,
                                     state="readonly", values=FORMATS, width=7)
        self.br_lbl = ttk.Label(grid, text="位元率", style="Sub.TLabel")
        self.br_var = tk.StringVar(value="320k")
        self.br_menu = ttk.Combobox(grid, textvariable=self.br_var,
                                    state="readonly", values=BITRATES, width=7)

        # 分割專用參數
        self.seg_lbl = ttk.Label(grid, text="每段長度（分鐘）", style="Sub.TLabel")
        self.seg_var = tk.StringVar(value="5")
        self.seg_spin = ttk.Spinbox(grid, textvariable=self.seg_var,
                                    from_=1, to=999, width=6)

        # 標準化專用參數
        self.norm_lbl = ttk.Label(grid, text="標準", style="Sub.TLabel")
        self.norm_var = tk.StringVar(value=list(NORM_PRESETS)[1])
        self.norm_menu = ttk.Combobox(grid, textvariable=self.norm_var,
                                      state="readonly",
                                      values=list(NORM_PRESETS), width=20)
        self._grid = grid

        # 輸出位置
        out_row = ttk.Frame(opt_card, style="Card.TFrame")
        out_row.pack(fill="x", pady=(10, 0))
        ttk.Label(out_row, text="輸出位置", style="Sub.TLabel").pack(anchor="w")
        out_inner = ttk.Frame(out_row, style="Card.TFrame")
        out_inner.pack(fill="x", pady=(2, 0))
        self.out_var = tk.StringVar(value=str(HERE / "output"))
        ttk.Entry(out_inner, textvariable=self.out_var).pack(
            side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(out_inner, text="選擇…", style="Ghost.TButton",
                   command=self._choose_output).pack(side="left")

        # --- 進度卡片 ---
        _, prog_card = self._card(self.root, "處理進度")

        self.file_lbl = ttk.Label(prog_card, text="尚未開始", style="Card.TLabel")
        self.file_lbl.pack(anchor="w", pady=(8, 2))

        cur_row = ttk.Frame(prog_card, style="Card.TFrame")
        cur_row.pack(fill="x")
        self.cur_bar = ttk.Progressbar(cur_row, maximum=100,
                                       style="Accent.Horizontal.TProgressbar")
        self.cur_bar.pack(side="left", fill="x", expand=True)
        self.cur_pct_lbl = ttk.Label(cur_row, text="0%", style="Sub.TLabel",
                                     width=5, anchor="e")
        self.cur_pct_lbl.pack(side="left", padx=(8, 0))

        total_head = ttk.Frame(prog_card, style="Card.TFrame")
        total_head.pack(fill="x", pady=(10, 2))
        ttk.Label(total_head, text="整體進度", style="Sub.TLabel").pack(side="left")
        self.total_lbl = ttk.Label(total_head, text="0 / 0", style="Sub.TLabel")
        self.total_lbl.pack(side="right")
        self.total_bar = ttk.Progressbar(prog_card, maximum=100,
                                         style="Accent.Horizontal.TProgressbar")
        self.total_bar.pack(fill="x")

        self.status = ttk.Label(prog_card, text="就緒", style="Sub.TLabel")
        self.status.pack(anchor="w", pady=(8, 0))

        # --- 執行按鈕列 ---
        run_row = ttk.Frame(self.root)
        run_row.pack(fill="x", padx=16, pady=(0, 8))
        self.run_btn = ttk.Button(run_row, text="開始處理",
                                  style="Accent.TButton", command=self._run)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(run_row, text="停止", style="Stop.TButton",
                                   command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.log_btn = ttk.Button(run_row, text="顯示詳細日誌 ▾",
                                  style="Ghost.TButton", command=self._toggle_log)
        self.log_btn.pack(side="right")

        # --- 日誌（預設收合）---
        self.log_outer = tk.Frame(self.root, bg=CARD,
                                  highlightbackground=BORDER,
                                  highlightthickness=1, bd=0)
        log_inner = ttk.Frame(self.log_outer, style="Card.TFrame")
        log_inner.pack(fill="both", expand=True, padx=10, pady=8)
        self.log = tk.Text(log_inner, height=8, wrap="word", state="disabled",
                           bg="#FAFAFC", fg=SUBTEXT, font=FONT_MONO, bd=0,
                           highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(log_inner, command=self.log.yview)
        lsb.pack(side="left", fill="y")
        self.log.config(yscrollcommand=lsb.set)
        self.log_visible = False

    def _toggle_log(self):
        if self.log_visible:
            self.log_outer.pack_forget()
            self.log_btn.config(text="顯示詳細日誌 ▾")
        else:
            self.log_outer.pack(fill="both", padx=16, pady=(0, 12))
            self.log_btn.config(text="隱藏詳細日誌 ▴")
        self.log_visible = not self.log_visible

    def _on_op_change(self):
        """依操作顯示對應的參數欄位。"""
        op, _, uses_gpu = OPERATIONS[self.op_var.get()]
        self.dev_menu.config(state="readonly" if uses_gpu else "disabled")

        for w in (self.fmt_lbl, self.fmt_menu, self.br_lbl, self.br_menu,
                  self.seg_lbl, self.seg_spin, self.norm_lbl, self.norm_menu):
            w.grid_forget()
        if op == "convert":
            self.fmt_lbl.grid(row=0, column=2, sticky="w")
            self.fmt_menu.grid(row=1, column=2, sticky="w", padx=(0, 14))
            self.br_lbl.grid(row=0, column=3, sticky="w")
            self.br_menu.grid(row=1, column=3, sticky="w")
        elif op == "split":
            self.seg_lbl.grid(row=0, column=2, sticky="w")
            self.seg_spin.grid(row=1, column=2, sticky="w")
        elif op == "normalize":
            self.norm_lbl.grid(row=0, column=2, sticky="w")
            self.norm_menu.grid(row=1, column=2, sticky="w")

    # ------------------------------------------------------------- 檔案操作
    def _on_drop(self, event):
        # tkdnd 用 {} 包住含空白的路徑，splitlist 可正確拆解
        for p in self.root.tk.splitlist(event.data):
            self._add_path(p)

    def _add_path(self, p):
        p = str(Path(p))
        if os.path.isfile(p) and p not in self.files:
            self.files.append(p)
            self.listbox.insert(tk.END, Path(p).name + "　—　" + p)
            self._update_count()

    def _update_count(self):
        self.count_lbl.config(text=f"{len(self.files)} 個檔案")

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="選擇音訊 / 影片檔",
            filetypes=[("媒體檔", "*.mp3 *.wav *.flac *.m4a *.aac *.ogg "
                                  "*.opus *.mp4 *.mkv *.mov *.avi *.webm "
                                  "*.flv *.wmv"),
                       ("所有檔案", "*.*")])
        for p in paths:
            self._add_path(p)

    def _add_folder(self):
        d = filedialog.askdirectory(title="選擇資料夾")
        if d:
            for p in sorted(Path(d).iterdir()):
                if p.is_file():
                    self._add_path(str(p))

    def _remove_sel(self):
        for i in reversed(self.listbox.curselection()):
            del self.files[i]
            self.listbox.delete(i)
        self._update_count()

    def _clear(self):
        self.files.clear()
        self.listbox.delete(0, tk.END)
        self._update_count()

    def _choose_output(self):
        _, kind, _ = OPERATIONS[self.op_var.get()]
        if kind == "file":
            p = filedialog.asksaveasfilename(
                title="輸出 MP3", defaultextension=".mp3",
                filetypes=[("MP3", "*.mp3")])
        else:
            p = filedialog.askdirectory(title="選擇輸出資料夾")
        if p:
            self.out_var.set(p)

    # --------------------------------------------------------------- 執行
    def _log(self, msg):
        self.log.config(state="normal")
        self.log.insert(tk.END, msg)
        self.log.see(tk.END)
        self.log.config(state="disabled")

    def _run(self):
        if not self.files:
            messagebox.showwarning("沒有檔案", "請先加入要處理的檔案。")
            return
        if self.running:
            messagebox.showinfo("處理中", "目前已有工作在執行。")
            return

        op, kind, _ = OPERATIONS[self.op_var.get()]
        out = self.out_var.get().strip()
        if not out:
            messagebox.showwarning("缺少輸出", "請設定輸出位置。")
            return

        # 剪輯是互動式操作：開波形編輯視窗，輸出由編輯器觸發
        if op == "cut":
            Path(out).mkdir(parents=True, exist_ok=True)
            sel = self.listbox.curselection()
            idx = sel[0] if sel else 0
            f = self.files[idx]
            if len(self.files) > 1:
                self.status.config(
                    text=f"剪輯一次一個檔案，已開啟：{Path(f).name}"
                         "（想剪別的檔請先在清單中點選）")
            CutEditor(self, f, Path(out))
            return

        # 組命令，每個元素是一條完整命令 (list)
        cmds = []
        base = [sys.executable, str(AUDIO_TOOL)]
        dev = self.dev_var.get()

        if op == "merge":
            cmds.append(base + ["merge"] + self.files + ["-o", out])
        else:
            Path(out).mkdir(parents=True, exist_ok=True)
            for f in self.files:
                stem = Path(f).stem
                if op == "extract":
                    cmds.append(base + ["extract", f, "-o",
                                        str(Path(out) / (stem + ".mp3"))])
                elif op == "convert":
                    dst = Path(out) / f"{stem}.{self.fmt_var.get()}"
                    cmds.append(base + ["convert", f, "-o", str(dst),
                                        "--bitrate", self.br_var.get()])
                elif op == "normalize":
                    ext = Path(f).suffix.lower()
                    if ext not in SUPPORTED_OUT_EXTS:
                        ext = ".mp3"
                    dst = Path(out) / f"{stem}_norm{ext}"
                    cmds.append(base + ["normalize", f, "-o", str(dst)]
                                + NORM_PRESETS[self.norm_var.get()])
                elif op == "split":
                    try:
                        secs = max(1, int(float(self.seg_var.get()) * 60))
                    except ValueError:
                        messagebox.showwarning("參數錯誤", "每段長度請輸入數字（分鐘）。")
                        return
                    cmds.append(base + ["split", f, "-o", out,
                                        "--seconds", str(secs)])
                elif op == "vocals":
                    cmds.append(base + ["vocals", f, "-o", out, "-d", dev])
                elif op == "trim":
                    cmds.append(base + ["trim", f, "-o",
                                        str(Path(out) / (stem + "_clean.mp3"))])
                elif op == "pipeline":
                    cmds.append(base + ["pipeline", f, "-o",
                                        str(Path(out) / (stem + "_vocal_clean.mp3")),
                                        "-d", dev])

        self._start_cmds(cmds, self.op_var.get())

    def _start_cmds(self, cmds, title):
        """啟動一批命令並更新進度 UI（也供剪輯編輯器使用）。"""
        if self.running:
            messagebox.showinfo("處理中", "目前已有工作在執行。")
            return
        self.running = True
        self.stop_flag.clear()
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.total_bar.config(value=0)
        self.cur_bar.config(value=0)
        self.cur_pct_lbl.config(text="0%")
        self.total_lbl.config(text=f"0 / {len(cmds)}")
        self.status.config(text="處理中…")
        self._log(f"\n===== 開始:{title}({len(cmds)} 項)=====\n")
        threading.Thread(target=self._worker, args=(cmds,), daemon=True).start()

    def _stop(self):
        if not self.running:
            return
        self.stop_flag.set()
        self.status.config(text="正在停止…")
        proc = self.proc
        if proc is not None:
            # 用 taskkill /T 連同 demucs / ffmpeg 子程序一起結束
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=_NO_WINDOW)
            else:
                proc.terminate()

    def _worker(self, cmds):
        # PYTHONUNBUFFERED=1：強制子程序即時輸出，否則 Demucs 進度條會卡在
        # 緩衝區裡，畫面看起來像當機。
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        n = len(cmds)
        ok = 0
        for i, cmd in enumerate(cmds, 1):
            if self.stop_flag.is_set():
                break
            # cmd = [python, audio_tool.py, <op>, <input...>, ...]
            name = next((Path(c).name for c in cmd[3:] if os.path.sep in c),
                        cmd[2])
            self.queue.put(("file", i, n, name))
            self.queue.put(("log", f"\n--- [{i}/{n}] {name} ---\n"))
            try:
                self.proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                    errors="replace", bufsize=1, env=env,
                    creationflags=_NO_WINDOW)
                # 逐字讀取，並把 \r（進度條更新）與 \n（一般訊息）分開處理，
                # 否則 Demucs 的 tqdm 進度條（用 \r）會看起來像卡住。
                buf = ""
                while True:
                    ch = self.proc.stdout.read(1)
                    if not ch:
                        break
                    if ch == "\n":
                        self.queue.put(("log", buf + "\n"))
                        buf = ""
                    elif ch == "\r":
                        line = buf.strip()
                        buf = ""
                        if not line:
                            continue
                        m = _PCT_RE.search(line)
                        if m:
                            self.queue.put(("pct", i, n, int(m.group(1))))
                        self.queue.put(("sub", line))
                    else:
                        buf += ch
                if buf.strip():
                    self.queue.put(("log", buf + "\n"))
                self.proc.wait()
                if self.proc.returncode == 0:
                    ok += 1
                    self.queue.put(("pct", i, n, 100))
                elif not self.stop_flag.is_set():
                    self.queue.put(("log", f"[失敗] 回傳碼 {self.proc.returncode}\n"))
            except Exception as e:
                self.queue.put(("log", f"[錯誤] {e}\n"))
            finally:
                self.proc = None
        stopped = self.stop_flag.is_set()
        msg = f"已停止（完成 {ok}/{n} 項）" if stopped else f"完成:成功 {ok}/{n} 項"
        self.queue.put(("done", ok, n, msg))

    def _drain_queue(self):
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._log(item[1])
                elif kind == "file":
                    _, i, n, name = item
                    self.file_lbl.config(text=name)
                    self.total_lbl.config(text=f"{i - 1} / {n}")
                    self.cur_bar.config(value=0)
                    self.cur_pct_lbl.config(text="0%")
                    self.total_bar.config(value=(i - 1) / n * 100)
                elif kind == "pct":
                    _, i, n, pct = item
                    self.cur_bar.config(value=pct)
                    self.cur_pct_lbl.config(text=f"{pct}%")
                    self.total_bar.config(value=((i - 1) + pct / 100) / n * 100)
                    if pct >= 100:
                        self.total_lbl.config(text=f"{i} / {n}")
                elif kind == "sub":
                    self.status.config(text=item[1][:90])
                elif kind == "done":
                    _, ok, n, msg = item
                    self._log(f"\n===== {msg} =====\n")
                    self.status.config(text=msg)
                    self.file_lbl.config(text="尚未開始" if ok == 0 else "全部完成")
                    self.running = False
                    self.run_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    messagebox.showinfo("完成", msg)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)


class CutEditor(tk.Toplevel):
    """剪輯視窗：在波形上拖曳選取片段 → 試聽 → 加入移除清單 → 輸出。"""

    WAVE_COLOR   = "#9CC3EE"   # 波形
    SEL_FILL     = "#D9EAFB"   # 目前選取
    REM_FILL     = "#FDE3E1"   # 已排定移除
    REM_EDGE     = "#FF3B30"

    def __init__(self, app, file_path, out_dir):
        super().__init__(app.root)
        self.app = app
        self.path = Path(file_path)
        self.out_dir = Path(out_dir)
        self.title(f"剪輯 — {self.path.name}")
        self.geometry("800x560")
        self.minsize(680, 500)
        self.configure(bg=BG)
        self.transient(app.root)

        self.duration = 0.0
        self.peaks = None            # 波形峰值 0~1
        self.load_error = None
        self.sel = None              # 目前選取 (start_s, end_s)
        self.removed = []            # 排定移除的 [(start_s, end_s)]
        self.play_proc = None
        self._anchor = None

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        threading.Thread(target=self._load_waveform, daemon=True).start()
        self.after(100, self._poll_loaded)

    # ------------------------------------------------------------- UI
    def _build(self):
        head = ttk.Frame(self)
        head.pack(fill="x", padx=16, pady=(12, 6))
        ttk.Label(head, text=self.path.name, style="Title.TLabel").pack(side="left")
        self.dur_lbl = ttk.Label(head, text="載入中…", style="Bg.TLabel",
                                 foreground=SUBTEXT)
        self.dur_lbl.pack(side="right")

        # 波形卡片
        wave_outer = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                              highlightthickness=1, bd=0)
        wave_outer.pack(fill="x", padx=16, pady=(0, 8))
        self.canvas = tk.Canvas(wave_outer, height=150, bg=CARD, bd=0,
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="x", padx=8, pady=8)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<Configure>", lambda e: self._render())

        hint = ("在波形上按住拖曳選取片段 → 試聽確認 → 加入移除清單；"
                "也可直接輸入時間。")
        ttk.Label(self, text=hint, style="Bg.TLabel",
                  foreground=SUBTEXT, font=FONT_SM).pack(
            anchor="w", padx=18, pady=(0, 6))

        # 選取控制列
        selrow_outer = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                                highlightthickness=1, bd=0)
        selrow_outer.pack(fill="x", padx=16, pady=(0, 8))
        selrow = ttk.Frame(selrow_outer, style="Card.TFrame")
        selrow.pack(fill="x", padx=12, pady=10)

        ttk.Label(selrow, text="選取", style="Sub.TLabel").pack(side="left")
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        ttk.Entry(selrow, textvariable=self.start_var, width=10).pack(
            side="left", padx=(8, 2))
        ttk.Label(selrow, text="—", style="Card.TLabel").pack(side="left")
        ttk.Entry(selrow, textvariable=self.end_var, width=10).pack(
            side="left", padx=(2, 8))
        ttk.Button(selrow, text="套用", style="Ghost.TButton",
                   command=self._apply_entries).pack(side="left", padx=(0, 12))

        self.play_btn = ttk.Button(selrow, text="▶ 試聽選取", style="Ghost.TButton",
                                   command=self._preview)
        self.play_btn.pack(side="left", padx=(0, 6))
        ttk.Button(selrow, text="■ 停止", style="Ghost.TButton",
                   command=self._stop_preview).pack(side="left", padx=(0, 12))

        ttk.Button(selrow, text="＋ 加入移除清單", style="Accent.TButton",
                   command=self._add_removed).pack(side="right")

        # 移除清單卡片
        list_outer = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                              highlightthickness=1, bd=0)
        list_outer.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        list_inner = ttk.Frame(list_outer, style="Card.TFrame")
        list_inner.pack(fill="both", expand=True, padx=12, pady=10)

        ttk.Label(list_inner, text="要移除的片段", style="CardBold.TLabel").pack(
            anchor="w")
        body = ttk.Frame(list_inner, style="Card.TFrame")
        body.pack(fill="both", expand=True, pady=(6, 0))
        self.rem_list = tk.Listbox(
            body, height=4, activestyle="none", bg="#FAFAFC", fg=TEXT,
            font=FONT, bd=0, highlightthickness=1,
            highlightbackground=BORDER, selectbackground=ACCENT,
            selectforeground="#FFFFFF")
        self.rem_list.pack(side="left", fill="both", expand=True)
        rsb = ttk.Scrollbar(body, command=self.rem_list.yview)
        rsb.pack(side="left", fill="y")
        self.rem_list.config(yscrollcommand=rsb.set)

        lb = ttk.Frame(list_inner, style="Card.TFrame")
        lb.pack(fill="x", pady=(6, 0))
        ttk.Button(lb, text="刪除選取項目", style="Ghost.TButton",
                   command=self._del_removed).pack(side="left", padx=(0, 6))
        ttk.Button(lb, text="清空", style="Ghost.TButton",
                   command=self._clear_removed).pack(side="left")
        self.total_removed_lbl = ttk.Label(lb, text="", style="Sub.TLabel")
        self.total_removed_lbl.pack(side="right")

        # 底部：輸出
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(bottom, text="輸出剪輯結果", style="Accent.TButton",
                   command=self._export).pack(side="right")
        ttk.Button(bottom, text="取消", style="Ghost.TButton",
                   command=self._close).pack(side="right", padx=(0, 8))

    # ------------------------------------------------------- 波形載入
    def _load_waveform(self):
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(self.path)
            self.duration = len(audio) / 1000.0
            mono = audio.set_channels(1)
            samples = mono.get_array_of_samples()
            n = len(samples)
            buckets = 2000
            peaks = []
            maxamp = float(mono.max_possible_amplitude) or 1.0
            step = max(1, n // buckets)
            for i in range(0, n - step + 1, step):
                chunk = samples[i:i + step:max(1, step // 50)]  # 每桶取樣 ~50 點
                peaks.append(max(abs(v) for v in chunk) / maxamp)
            self.peaks = peaks or [0.0]
        except Exception as e:
            self.load_error = str(e)

    def _poll_loaded(self):
        if self.load_error is not None:
            messagebox.showerror("讀取失敗", f"無法讀取音檔：\n{self.load_error}",
                                 parent=self)
            self._close()
            return
        if self.peaks is None:
            self.after(100, self._poll_loaded)
            return
        self.dur_lbl.config(text=f"總長 {fmt_time(self.duration)}")
        self._render()

    # ------------------------------------------------------- 繪圖
    def _x2t(self, x):
        w = max(1, self.canvas.winfo_width())
        return min(max(0.0, x / w * self.duration), self.duration)

    def _t2x(self, t):
        w = max(1, self.canvas.winfo_width())
        return t / max(0.001, self.duration) * w

    def _render(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if self.peaks is None or w < 10:
            c.create_text(w // 2, h // 2, text="波形載入中…",
                          fill=SUBTEXT, font=FONT)
            return

        # 已排定移除的片段（淡紅）
        for s, e in self.removed:
            c.create_rectangle(self._t2x(s), 0, self._t2x(e), h,
                               fill=self.REM_FILL, outline=self.REM_EDGE,
                               dash=(2, 2))
        # 目前選取（淡藍）
        if self.sel:
            s, e = self.sel
            c.create_rectangle(self._t2x(s), 0, self._t2x(e), h,
                               fill=self.SEL_FILL, outline=ACCENT)

        # 波形
        mid = h / 2
        n = len(self.peaks)
        for x in range(w):
            p = self.peaks[min(n - 1, int(x / w * n))]
            y = max(1.0, p * (h / 2 - 6))
            c.create_line(x, mid - y, x, mid + y, fill=self.WAVE_COLOR)
        c.create_line(0, mid, w, mid, fill=BORDER)

        # 時間標記
        c.create_text(4, h - 8, text="0:00", anchor="w",
                      fill=SUBTEXT, font=FONT_SM)
        c.create_text(w - 4, h - 8, text=fmt_time(self.duration), anchor="e",
                      fill=SUBTEXT, font=FONT_SM)

    # ------------------------------------------------------- 滑鼠選取
    def _on_press(self, event):
        if self.peaks is None:
            return
        self._anchor = self._x2t(event.x)
        self.sel = None
        self._render()

    def _on_drag(self, event):
        if self.peaks is None or self._anchor is None:
            return
        t = self._x2t(event.x)
        a, b = sorted((self._anchor, t))
        if b - a >= 0.05:
            self.sel = (a, b)
            self.start_var.set(fmt_time(a))
            self.end_var.set(fmt_time(b))
            self._render()

    def _apply_entries(self):
        try:
            a = parse_time(self.start_var.get())
            b = parse_time(self.end_var.get())
        except ValueError:
            messagebox.showwarning("時間格式錯誤",
                                   "請用 秒 或 分:秒，例如 90 或 1:30.5",
                                   parent=self)
            return
        a = min(max(0.0, a), self.duration)
        b = min(max(0.0, b), self.duration)
        if b <= a:
            messagebox.showwarning("範圍錯誤", "結束時間必須大於開始時間。",
                                   parent=self)
            return
        self.sel = (a, b)
        self._render()

    # ------------------------------------------------------- 試聽
    def _preview(self):
        if not self.sel:
            messagebox.showinfo("沒有選取", "請先在波形上拖曳選取片段。",
                                parent=self)
            return
        if shutil.which("ffplay") is None:
            messagebox.showinfo("無法試聽", "找不到 ffplay（隨 ffmpeg 安裝）。",
                                parent=self)
            return
        self._stop_preview()
        a, b = self.sel
        self.play_proc = subprocess.Popen(
            ["ffplay", "-hide_banner", "-loglevel", "error", "-nodisp",
             "-autoexit", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
             str(self.path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)

    def _stop_preview(self):
        if self.play_proc is not None and self.play_proc.poll() is None:
            self.play_proc.terminate()
        self.play_proc = None

    # ------------------------------------------------------- 移除清單
    def _add_removed(self):
        if not self.sel:
            messagebox.showinfo("沒有選取", "請先在波形上拖曳選取片段。",
                                parent=self)
            return
        self.removed.append(self.sel)
        self.removed.sort()
        self.sel = None
        self._refresh_removed()

    def _del_removed(self):
        for i in reversed(self.rem_list.curselection()):
            del self.removed[i]
        self._refresh_removed()

    def _clear_removed(self):
        self.removed.clear()
        self._refresh_removed()

    def _refresh_removed(self):
        self.rem_list.delete(0, tk.END)
        total = 0.0
        for i, (a, b) in enumerate(self.removed, 1):
            self.rem_list.insert(
                tk.END,
                f"{i}.  {fmt_time(a)} — {fmt_time(b)}   （{b - a:.1f} 秒）")
            total += b - a
        self.total_removed_lbl.config(
            text=f"共移除 {total:.1f} 秒" if self.removed else "")
        self._render()

    # ------------------------------------------------------- 輸出
    def _export(self):
        if not self.removed:
            messagebox.showinfo("沒有片段", "請先加入至少一個要移除的片段。",
                                parent=self)
            return
        ext = self.path.suffix.lower()
        if ext not in SUPPORTED_OUT_EXTS:
            ext = ".mp3"
        out = self.out_dir / f"{self.path.stem}_cut{ext}"

        cmd = [sys.executable, str(AUDIO_TOOL), "cut", str(self.path),
               "-o", str(out)]
        for a, b in self.removed:
            cmd += ["--remove", f"{a:.3f}-{b:.3f}"]

        self._close()
        self.app._start_cmds([cmd], "剪輯")

    def _close(self):
        self._stop_preview()
        self.destroy()


def main():
    if not AUDIO_TOOL.is_file():
        print(f"找不到 {AUDIO_TOOL}")
        sys.exit(1)
    # 高 DPI 螢幕下讓文字清晰
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = TkinterDnD.Tk() if _DND else tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
