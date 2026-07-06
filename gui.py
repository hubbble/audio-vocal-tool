#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
音訊處理工具 — 拖放式圖形介面 (GUI)

功能：
  - 把檔案 / 影片直接拖進視窗（或用「加入檔案」按鈕）
  - 操作：影片轉音檔 / 轉檔 / 分割 / 剪輯 / 標準化 / 人聲分離 / 去靜音 / 合併
  - 獨立的進度區：目前檔案進度條 + 整體進度條
  - 可隨時停止處理；介面語言可切換（繁體中文 / English）

執行（用 venv 裡的 python，才有 torch / demucs）：
  .venv312\Scripts\python.exe gui.py

底層是呼叫 audio_tool.py，因此所有功能與 CLI 一致。
"""

import json
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
CONFIG_FILE = HERE / "gui_settings.json"
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

# ---------------------------------------------------------------- 多國語言
LANG_NAMES = {"zh": "繁體中文", "en": "English"}

I18N = {
    "zh": {
        "app_title": "音訊處理工具",
        "input_files": "輸入檔案",
        "drop_hint": "拖曳檔案到清單中",
        "no_dnd_hint": "未安裝 tkinterdnd2，請用按鈕加入",
        "add_files": "＋ 加入檔案",
        "add_folder": "＋ 加入資料夾",
        "remove_selected": "移除選取",
        "clear": "清空",
        "n_files": "{} 個檔案",
        "settings": "設定",
        "operation": "操作",
        "device": "裝置",
        "out_format": "輸出格式",
        "bitrate": "位元率",
        "seg_len": "每段長度（分鐘）",
        "norm_std": "標準",
        "out_location": "輸出位置",
        "choose": "選擇…",
        "progress_title": "處理進度",
        "not_started": "尚未開始",
        "overall": "整體進度",
        "ready": "就緒",
        "start": "開始處理",
        "stop": "停止",
        "show_log": "顯示詳細日誌 ▾",
        "hide_log": "隱藏詳細日誌 ▴",
        "done_all": "全部完成",
        "status_processing": "處理中…",
        "status_stopping": "正在停止…",
        "status_stopped": "已停止（完成 {}/{} 項）",
        "status_done": "完成：成功 {}/{} 項",
        "cut_one_at_a_time": "剪輯一次一個檔案，已開啟:{}（想剪別的檔請先在清單中點選）",
        "log_start": "\n===== 開始:{}（{} 項）=====\n",
        "log_fail": "[失敗] 回傳碼 {}\n",
        "log_err": "[錯誤] {}\n",
        "msg_no_files_t": "沒有檔案",
        "msg_no_files": "請先加入要處理的檔案。",
        "msg_busy_t": "處理中",
        "msg_busy": "目前已有工作在執行。",
        "msg_no_out_t": "缺少輸出",
        "msg_no_out": "請設定輸出位置。",
        "msg_bad_seg_t": "參數錯誤",
        "msg_bad_seg": "每段長度請輸入數字（分鐘）。",
        "msg_done_t": "完成",
        "spk_count": "說話人數（0=自動）",
        "msg_no_token_t": "需要 Hugging Face token",
        "msg_no_token": "分離說話人需要免費的 Hugging Face token：\n\n"
                        "1. 到 huggingface.co 註冊帳號\n"
                        "2. 到 pyannote/speaker-diarization-community-1\n"
                        "    模型頁面接受條款\n"
                        "3. 到 Settings → Access Tokens 建立 Read token\n"
                        "4. 把 token 存成本資料夾裡的 .hf_token 檔\n"
                        "    （純文字、一行），再重新執行即可。",
        "dlg_media": "媒體檔",
        "dlg_all": "所有檔案",
        "dlg_pick_files": "選擇音訊 / 影片檔",
        "dlg_pick_folder": "選擇資料夾",
        "dlg_out_mp3": "輸出 MP3",
        "dlg_pick_outdir": "選擇輸出資料夾",
        "op_pipeline": "人聲分離 ＋ 去靜音",
        "op_vocals": "人聲分離",
        "op_speakers": "分離不同說話人",
        "op_trim": "去除靜音",
        "op_cut": "剪輯（自選片段移除）",
        "op_normalize": "音量標準化",
        "op_convert": "音訊轉檔",
        "op_split": "分割音檔",
        "op_extract": "影片轉音檔",
        "op_merge": "合併成一個 MP3",
        "norm_s14": "-14 LUFS（串流平台）",
        "norm_g16": "-16 LUFS（通用）",
        "norm_b23": "-23 LUFS（廣播）",
        "norm_peak": "峰值 -1 dBFS（快速）",
        "ed_title": "剪輯 — {}",
        "ed_loading": "載入中…",
        "ed_total": "總長 {}",
        "ed_hint": "在波形上按住拖曳選取片段 → 試聽確認 → 加入移除清單；也可直接輸入時間。",
        "ed_select": "選取",
        "ed_apply": "套用",
        "ed_play": "▶ 試聽選取",
        "ed_stop": "■ 停止",
        "ed_add": "＋ 加入移除清單",
        "ed_remove_list": "要移除的片段",
        "ed_del_item": "刪除選取項目",
        "ed_clear": "清空",
        "ed_total_removed": "共移除 {:.1f} 秒",
        "ed_export": "輸出剪輯結果",
        "ed_cancel": "取消",
        "ed_wave_loading": "波形載入中…",
        "ed_item": "{}.  {} — {}   （{:.1f} 秒）",
        "ed_load_fail_t": "讀取失敗",
        "ed_load_fail": "無法讀取音檔:\n{}",
        "ed_no_sel_t": "沒有選取",
        "ed_no_sel": "請先在波形上拖曳選取片段。",
        "ed_no_ffplay_t": "無法試聽",
        "ed_no_ffplay": "找不到 ffplay（隨 ffmpeg 安裝）。",
        "ed_bad_time_t": "時間格式錯誤",
        "ed_bad_time": "請用 秒 或 分:秒，例如 90 或 1:30.5",
        "ed_bad_range_t": "範圍錯誤",
        "ed_bad_range": "結束時間必須大於開始時間。",
        "ed_no_seg_t": "沒有片段",
        "ed_no_seg": "請先加入至少一個要移除的片段。",
        "ed_export_title": "剪輯",
    },
    "en": {
        "app_title": "Audio Toolbox",
        "input_files": "Input Files",
        "drop_hint": "Drag & drop files into the list",
        "no_dnd_hint": "tkinterdnd2 not installed — use the buttons",
        "add_files": "＋ Add Files",
        "add_folder": "＋ Add Folder",
        "remove_selected": "Remove Selected",
        "clear": "Clear",
        "n_files": "{} file(s)",
        "settings": "Settings",
        "operation": "Operation",
        "device": "Device",
        "out_format": "Format",
        "bitrate": "Bitrate",
        "seg_len": "Segment length (min)",
        "norm_std": "Standard",
        "out_location": "Output Location",
        "choose": "Browse…",
        "progress_title": "Progress",
        "not_started": "Not started",
        "overall": "Overall",
        "ready": "Ready",
        "start": "Start",
        "stop": "Stop",
        "show_log": "Show log ▾",
        "hide_log": "Hide log ▴",
        "done_all": "All done",
        "status_processing": "Processing…",
        "status_stopping": "Stopping…",
        "status_stopped": "Stopped ({}/{} done)",
        "status_done": "Done: {}/{} succeeded",
        "cut_one_at_a_time": "Cut works one file at a time — opened: {} "
                             "(select another file in the list to cut it)",
        "log_start": "\n===== Start: {} ({} task(s)) =====\n",
        "log_fail": "[Failed] exit code {}\n",
        "log_err": "[Error] {}\n",
        "msg_no_files_t": "No Files",
        "msg_no_files": "Please add files to process first.",
        "msg_busy_t": "Busy",
        "msg_busy": "A job is already running.",
        "msg_no_out_t": "No Output",
        "msg_no_out": "Please set the output location.",
        "msg_bad_seg_t": "Invalid Parameter",
        "msg_bad_seg": "Segment length must be a number (minutes).",
        "msg_done_t": "Done",
        "spk_count": "Speakers (0=auto)",
        "msg_no_token_t": "Hugging Face Token Required",
        "msg_no_token": "Speaker separation needs a free Hugging Face token:\n\n"
                        "1. Sign up at huggingface.co\n"
                        "2. Accept the terms on the model page\n"
                        "    pyannote/speaker-diarization-community-1\n"
                        "3. Create a Read token in Settings → Access Tokens\n"
                        "4. Save it as a .hf_token file (plain text, one line)\n"
                        "    in this folder, then run again.",
        "dlg_media": "Media files",
        "dlg_all": "All files",
        "dlg_pick_files": "Select audio / video files",
        "dlg_pick_folder": "Select folder",
        "dlg_out_mp3": "Output MP3",
        "dlg_pick_outdir": "Select output folder",
        "op_pipeline": "Isolate Vocals + Trim Silence",
        "op_vocals": "Isolate Vocals",
        "op_speakers": "Separate Speakers",
        "op_trim": "Trim Silence",
        "op_cut": "Cut (remove selected parts)",
        "op_normalize": "Normalize Volume",
        "op_convert": "Convert Format",
        "op_split": "Split Audio",
        "op_extract": "Extract Audio from Video",
        "op_merge": "Merge into one MP3",
        "norm_s14": "-14 LUFS (streaming)",
        "norm_g16": "-16 LUFS (general)",
        "norm_b23": "-23 LUFS (broadcast)",
        "norm_peak": "Peak -1 dBFS (fast)",
        "ed_title": "Cut — {}",
        "ed_loading": "Loading…",
        "ed_total": "Duration {}",
        "ed_hint": "Drag on the waveform to select a part → preview → add to "
                   "removal list; or type times directly.",
        "ed_select": "Selection",
        "ed_apply": "Apply",
        "ed_play": "▶ Preview",
        "ed_stop": "■ Stop",
        "ed_add": "＋ Add to Removal List",
        "ed_remove_list": "Parts to remove",
        "ed_del_item": "Delete Selected",
        "ed_clear": "Clear",
        "ed_total_removed": "{:.1f} s removed in total",
        "ed_export": "Export Result",
        "ed_cancel": "Cancel",
        "ed_wave_loading": "Loading waveform…",
        "ed_item": "{}.  {} — {}   ({:.1f} s)",
        "ed_load_fail_t": "Load Failed",
        "ed_load_fail": "Could not read the audio file:\n{}",
        "ed_no_sel_t": "No Selection",
        "ed_no_sel": "Drag on the waveform to select a part first.",
        "ed_no_ffplay_t": "Cannot Preview",
        "ed_no_ffplay": "ffplay not found (it comes with ffmpeg).",
        "ed_bad_time_t": "Invalid Time",
        "ed_bad_time": "Use seconds or min:sec, e.g. 90 or 1:30.5",
        "ed_bad_range_t": "Invalid Range",
        "ed_bad_range": "End time must be greater than start time.",
        "ed_no_seg_t": "No Parts",
        "ed_no_seg": "Add at least one part to remove first.",
        "ed_export_title": "Cut",
    },
}

_lang = "zh"


def set_lang(lang: str) -> None:
    global _lang
    _lang = lang if lang in I18N else "zh"


def T(key: str, *args) -> str:
    s = I18N[_lang].get(key) or I18N["zh"].get(key, key)
    return s.format(*args) if args else s


def load_lang() -> str:
    try:
        return json.loads(CONFIG_FILE.read_text("utf-8")).get("lang", "zh")
    except Exception:
        return "zh"


def save_lang(lang: str) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps({"lang": lang}), "utf-8")
    except Exception:
        pass


# 操作定義：代碼順序；輸出成單一檔案的操作；用到 GPU 的操作
OP_ORDER = ["pipeline", "vocals", "speakers", "trim", "cut", "normalize",
            "convert", "split", "extract", "merge"]
OP_FILE_OUT = {"merge"}
OP_GPU = {"pipeline", "vocals", "speakers"}


def has_hf_token() -> bool:
    """檢查是否已設定 Hugging Face token（speakers 功能需要）。"""
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"):
        return True
    return (HERE / ".hf_token").is_file()

# 標準化預設：代碼 -> 附加的 CLI 參數
NORM_ORDER = ["s14", "g16", "b23", "peak"]
NORM_ARGS = {
    "s14":  ["--mode", "lufs", "--lufs", "-14"],
    "g16":  ["--mode", "lufs", "--lufs", "-16"],
    "b23":  ["--mode", "lufs", "--lufs", "-23"],
    "peak": ["--mode", "peak", "--peak-dbfs", "-1"],
}

FORMATS  = ["mp3", "wav", "flac", "m4a", "ogg", "opus"]
BITRATES = ["320k", "256k", "192k", "128k"]
SUPPORTED_OUT_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus")

_PCT_RE = re.compile(r"(\d{1,3})(?:\.\d+)?%")
# tqdm / demucs 進度列，例如 " 95%|████▌   | 1117.35/1175.85 [00:39<00:01, 30.91seconds/s]"
_TQDM_RE = re.compile(r"^(\d{1,3})(?:\.\d+)?%\|")


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


class App:
    def __init__(self, root):
        self.root = root
        self.lang = load_lang()
        set_lang(self.lang)

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
        self.root.title(T("app_title"))

        # --- 標題 + 語言切換 ---
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=16, pady=(14, 10))
        ttk.Label(header, text=T("app_title"), style="Title.TLabel").pack(side="left")
        self.lang_var = tk.StringVar(value=LANG_NAMES[self.lang])
        lang_menu = ttk.Combobox(header, textvariable=self.lang_var,
                                 state="readonly",
                                 values=list(LANG_NAMES.values()), width=10)
        lang_menu.pack(side="right")
        lang_menu.bind("<<ComboboxSelected>>", self._on_lang_change)

        # --- 檔案卡片 ---
        hint = T("drop_hint") if _DND else T("no_dnd_hint")
        outer, files_card = self._card(self.root, T("input_files"), hint)
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
        ttk.Button(btns, text=T("add_files"), style="Ghost.TButton",
                   command=self._add_files).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text=T("add_folder"), style="Ghost.TButton",
                   command=self._add_folder).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text=T("remove_selected"), style="Ghost.TButton",
                   command=self._remove_sel).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text=T("clear"), style="Ghost.TButton",
                   command=self._clear).pack(side="left")
        self.count_lbl = ttk.Label(btns, text=T("n_files", 0), style="Sub.TLabel")
        self.count_lbl.pack(side="right")

        # --- 設定卡片 ---
        _, opt_card = self._card(self.root, T("settings"))
        grid = ttk.Frame(opt_card, style="Card.TFrame")
        grid.pack(fill="x", pady=(8, 0))

        ttk.Label(grid, text=T("operation"), style="Sub.TLabel").grid(
            row=0, column=0, sticky="w")
        op_labels = [T("op_" + c) for c in OP_ORDER]
        self._op_by_label = dict(zip(op_labels, OP_ORDER))
        self.op_var = tk.StringVar(value=op_labels[0])
        op_menu = ttk.Combobox(grid, textvariable=self.op_var, state="readonly",
                               values=op_labels, width=28)
        op_menu.grid(row=1, column=0, sticky="w", padx=(0, 14))
        op_menu.bind("<<ComboboxSelected>>", lambda e: self._on_op_change())

        ttk.Label(grid, text=T("device"), style="Sub.TLabel").grid(
            row=0, column=1, sticky="w")
        self.dev_var = tk.StringVar(value="auto")
        self.dev_menu = ttk.Combobox(grid, textvariable=self.dev_var,
                                     state="readonly",
                                     values=["auto", "cuda", "cpu"], width=7)
        self.dev_menu.grid(row=1, column=1, sticky="w", padx=(0, 14))

        # 轉檔專用參數
        self.fmt_lbl = ttk.Label(grid, text=T("out_format"), style="Sub.TLabel")
        self.fmt_var = tk.StringVar(value="mp3")
        self.fmt_menu = ttk.Combobox(grid, textvariable=self.fmt_var,
                                     state="readonly", values=FORMATS, width=7)
        self.br_lbl = ttk.Label(grid, text=T("bitrate"), style="Sub.TLabel")
        self.br_var = tk.StringVar(value="320k")
        self.br_menu = ttk.Combobox(grid, textvariable=self.br_var,
                                    state="readonly", values=BITRATES, width=7)

        # 分割專用參數
        self.seg_lbl = ttk.Label(grid, text=T("seg_len"), style="Sub.TLabel")
        self.seg_var = tk.StringVar(value="5")
        self.seg_spin = ttk.Spinbox(grid, textvariable=self.seg_var,
                                    from_=1, to=999, width=6)

        # 分離說話人專用參數
        self.spk_lbl = ttk.Label(grid, text=T("spk_count"), style="Sub.TLabel")
        self.spk_var = tk.StringVar(value="2")
        self.spk_spin = ttk.Spinbox(grid, textvariable=self.spk_var,
                                    from_=0, to=10, width=6)

        # 標準化專用參數
        norm_labels = [T("norm_" + c) for c in NORM_ORDER]
        self._norm_by_label = dict(zip(norm_labels, NORM_ORDER))
        self.norm_lbl = ttk.Label(grid, text=T("norm_std"), style="Sub.TLabel")
        self.norm_var = tk.StringVar(value=norm_labels[1])
        self.norm_menu = ttk.Combobox(grid, textvariable=self.norm_var,
                                      state="readonly",
                                      values=norm_labels, width=20)
        self._grid = grid

        # 輸出位置
        out_row = ttk.Frame(opt_card, style="Card.TFrame")
        out_row.pack(fill="x", pady=(10, 0))
        ttk.Label(out_row, text=T("out_location"), style="Sub.TLabel").pack(anchor="w")
        out_inner = ttk.Frame(out_row, style="Card.TFrame")
        out_inner.pack(fill="x", pady=(2, 0))
        self.out_var = tk.StringVar(value=str(HERE / "output"))
        ttk.Entry(out_inner, textvariable=self.out_var).pack(
            side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(out_inner, text=T("choose"), style="Ghost.TButton",
                   command=self._choose_output).pack(side="left")

        # --- 進度卡片 ---
        _, prog_card = self._card(self.root, T("progress_title"))

        self.file_lbl = ttk.Label(prog_card, text=T("not_started"),
                                  style="Card.TLabel")
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
        ttk.Label(total_head, text=T("overall"), style="Sub.TLabel").pack(side="left")
        self.total_lbl = ttk.Label(total_head, text="0 / 0", style="Sub.TLabel")
        self.total_lbl.pack(side="right")
        self.total_bar = ttk.Progressbar(prog_card, maximum=100,
                                         style="Accent.Horizontal.TProgressbar")
        self.total_bar.pack(fill="x")

        self.status = ttk.Label(prog_card, text=T("ready"), style="Sub.TLabel")
        self.status.pack(anchor="w", pady=(8, 0))

        # --- 執行按鈕列 ---
        run_row = ttk.Frame(self.root)
        run_row.pack(fill="x", padx=16, pady=(0, 8))
        self.run_btn = ttk.Button(run_row, text=T("start"),
                                  style="Accent.TButton", command=self._run)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(run_row, text=T("stop"), style="Stop.TButton",
                                   command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.log_btn = ttk.Button(run_row, text=T("show_log"),
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

    # ------------------------------------------------------------- 語言切換
    def _on_lang_change(self, event=None):
        code = {v: k for k, v in LANG_NAMES.items()}.get(self.lang_var.get(), "zh")
        if code == self.lang:
            return
        if self.running:
            # 處理中不重建 UI，避免打斷進度顯示
            self.lang_var.set(LANG_NAMES[self.lang])
            return
        self.lang = code
        set_lang(code)
        save_lang(code)

        # 保留目前狀態後整個重建介面
        files = list(self.files)
        out = self.out_var.get()
        op_code = self._op()
        dev = self.dev_var.get()
        for w in self.root.winfo_children():
            w.destroy()
        self.files = []
        self._build_ui()
        for f in files:
            self._add_path(f)
        self.out_var.set(out)
        self.op_var.set(T("op_" + op_code))
        self.dev_var.set(dev)
        self._on_op_change()

    def _op(self) -> str:
        """目前選擇的操作代碼。"""
        return self._op_by_label.get(self.op_var.get(), OP_ORDER[0])

    def _toggle_log(self):
        if self.log_visible:
            self.log_outer.pack_forget()
            self.log_btn.config(text=T("show_log"))
        else:
            self.log_outer.pack(fill="both", padx=16, pady=(0, 12))
            self.log_btn.config(text=T("hide_log"))
        self.log_visible = not self.log_visible

    def _on_op_change(self):
        """依操作顯示對應的參數欄位。"""
        op = self._op()
        self.dev_menu.config(state="readonly" if op in OP_GPU else "disabled")

        for w in (self.fmt_lbl, self.fmt_menu, self.br_lbl, self.br_menu,
                  self.seg_lbl, self.seg_spin, self.norm_lbl, self.norm_menu,
                  self.spk_lbl, self.spk_spin):
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
        elif op == "speakers":
            self.spk_lbl.grid(row=0, column=2, sticky="w")
            self.spk_spin.grid(row=1, column=2, sticky="w")

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
        self.count_lbl.config(text=T("n_files", len(self.files)))

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title=T("dlg_pick_files"),
            filetypes=[(T("dlg_media"), "*.mp3 *.wav *.flac *.m4a *.aac *.ogg "
                                        "*.opus *.mp4 *.mkv *.mov *.avi *.webm "
                                        "*.flv *.wmv"),
                       (T("dlg_all"), "*.*")])
        for p in paths:
            self._add_path(p)

    def _add_folder(self):
        d = filedialog.askdirectory(title=T("dlg_pick_folder"))
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
        if self._op() in OP_FILE_OUT:
            p = filedialog.asksaveasfilename(
                title=T("dlg_out_mp3"), defaultextension=".mp3",
                filetypes=[("MP3", "*.mp3")])
        else:
            p = filedialog.askdirectory(title=T("dlg_pick_outdir"))
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
            messagebox.showwarning(T("msg_no_files_t"), T("msg_no_files"))
            return
        if self.running:
            messagebox.showinfo(T("msg_busy_t"), T("msg_busy"))
            return

        op = self._op()
        out = self.out_var.get().strip()
        if not out:
            messagebox.showwarning(T("msg_no_out_t"), T("msg_no_out"))
            return

        # 分離說話人需要 Hugging Face token，先檢查並給說明
        if op == "speakers" and not has_hf_token():
            messagebox.showwarning(T("msg_no_token_t"), T("msg_no_token"))
            return

        # 剪輯是互動式操作：開波形編輯視窗，輸出由編輯器觸發
        if op == "cut":
            Path(out).mkdir(parents=True, exist_ok=True)
            sel = self.listbox.curselection()
            idx = sel[0] if sel else 0
            f = self.files[idx]
            if len(self.files) > 1:
                self.status.config(text=T("cut_one_at_a_time", Path(f).name))
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
                                + NORM_ARGS[self._norm_by_label.get(
                                    self.norm_var.get(), "g16")])
                elif op == "split":
                    try:
                        secs = max(1, int(float(self.seg_var.get()) * 60))
                    except ValueError:
                        messagebox.showwarning(T("msg_bad_seg_t"), T("msg_bad_seg"))
                        return
                    cmds.append(base + ["split", f, "-o", out,
                                        "--seconds", str(secs)])
                elif op == "vocals":
                    cmds.append(base + ["vocals", f, "-o", out, "-d", dev])
                elif op == "speakers":
                    try:
                        n_spk = max(0, int(self.spk_var.get()))
                    except ValueError:
                        n_spk = 2
                    cmds.append(base + ["speakers", f, "-o", out,
                                        "--speakers", str(n_spk), "-d", dev])
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
            messagebox.showinfo(T("msg_busy_t"), T("msg_busy"))
            return
        self.running = True
        self.stop_flag.clear()
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.total_bar.config(value=0)
        self.cur_bar.config(value=0)
        self.cur_pct_lbl.config(text="0%")
        self.total_lbl.config(text=f"0 / {len(cmds)}")
        self.status.config(text=T("status_processing"))
        self._log(T("log_start", title, len(cmds)))
        threading.Thread(target=self._worker, args=(cmds,), daemon=True).start()

    def _stop(self):
        if not self.running:
            return
        self.stop_flag.set()
        self.status.config(text=T("status_stopping"))
        proc = self.proc
        if proc is not None:
            # 用 taskkill /T 連同 demucs / ffmpeg 子程序一起結束
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=_NO_WINDOW)
            else:
                proc.terminate()

    def _emit_progress(self, line, i, n):
        """tqdm / demucs 進度列 → 進度條更新；回傳是否已處理（不進日誌）。"""
        m = _TQDM_RE.match(line)
        if not m:
            return False
        pct = min(100, int(float(m.group(1))))
        self.queue.put(("pct", i, n, pct))
        # 狀態列顯示乾淨版本：去掉方塊圖形，只留 "95% · 1117.4/1175.9 [00:39<00:01]"
        tail = line.rsplit("|", 1)[-1].strip()
        self.queue.put(("sub", f"{pct}% · {tail}" if tail else f"{pct}%"))
        return True

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
                        # tqdm 在非終端機環境會改用換行輸出進度列，
                        # 一樣要攔下來轉成進度條，不然會洗版日誌
                        line = buf.strip()
                        if line and not self._emit_progress(line, i, n):
                            self.queue.put(("log", buf + "\n"))
                        buf = ""
                    elif ch == "\r":
                        line = buf.strip()
                        buf = ""
                        if not line:
                            continue
                        if self._emit_progress(line, i, n):
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
                    self.queue.put(("log", T("log_fail", self.proc.returncode)))
            except Exception as e:
                self.queue.put(("log", T("log_err", e)))
            finally:
                self.proc = None
        if self.stop_flag.is_set():
            msg = T("status_stopped", ok, n)
        else:
            msg = T("status_done", ok, n)
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
                    self.file_lbl.config(
                        text=T("not_started") if ok == 0 else T("done_all"))
                    self.running = False
                    self.run_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    messagebox.showinfo(T("msg_done_t"), msg)
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
        self.title(T("ed_title", self.path.name))
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
        self.dur_lbl = ttk.Label(head, text=T("ed_loading"), style="Bg.TLabel",
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

        ttk.Label(self, text=T("ed_hint"), style="Bg.TLabel",
                  foreground=SUBTEXT, font=FONT_SM).pack(
            anchor="w", padx=18, pady=(0, 6))

        # 選取控制列
        selrow_outer = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                                highlightthickness=1, bd=0)
        selrow_outer.pack(fill="x", padx=16, pady=(0, 8))
        selrow = ttk.Frame(selrow_outer, style="Card.TFrame")
        selrow.pack(fill="x", padx=12, pady=10)

        ttk.Label(selrow, text=T("ed_select"), style="Sub.TLabel").pack(side="left")
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        ttk.Entry(selrow, textvariable=self.start_var, width=10).pack(
            side="left", padx=(8, 2))
        ttk.Label(selrow, text="—", style="Card.TLabel").pack(side="left")
        ttk.Entry(selrow, textvariable=self.end_var, width=10).pack(
            side="left", padx=(2, 8))
        ttk.Button(selrow, text=T("ed_apply"), style="Ghost.TButton",
                   command=self._apply_entries).pack(side="left", padx=(0, 12))

        self.play_btn = ttk.Button(selrow, text=T("ed_play"), style="Ghost.TButton",
                                   command=self._preview)
        self.play_btn.pack(side="left", padx=(0, 6))
        ttk.Button(selrow, text=T("ed_stop"), style="Ghost.TButton",
                   command=self._stop_preview).pack(side="left", padx=(0, 12))

        ttk.Button(selrow, text=T("ed_add"), style="Accent.TButton",
                   command=self._add_removed).pack(side="right")

        # 移除清單卡片
        list_outer = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                              highlightthickness=1, bd=0)
        list_outer.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        list_inner = ttk.Frame(list_outer, style="Card.TFrame")
        list_inner.pack(fill="both", expand=True, padx=12, pady=10)

        ttk.Label(list_inner, text=T("ed_remove_list"),
                  style="CardBold.TLabel").pack(anchor="w")
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
        ttk.Button(lb, text=T("ed_del_item"), style="Ghost.TButton",
                   command=self._del_removed).pack(side="left", padx=(0, 6))
        ttk.Button(lb, text=T("ed_clear"), style="Ghost.TButton",
                   command=self._clear_removed).pack(side="left")
        self.total_removed_lbl = ttk.Label(lb, text="", style="Sub.TLabel")
        self.total_removed_lbl.pack(side="right")

        # 底部：輸出
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(bottom, text=T("ed_export"), style="Accent.TButton",
                   command=self._export).pack(side="right")
        ttk.Button(bottom, text=T("ed_cancel"), style="Ghost.TButton",
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
            messagebox.showerror(T("ed_load_fail_t"),
                                 T("ed_load_fail", self.load_error), parent=self)
            self._close()
            return
        if self.peaks is None:
            self.after(100, self._poll_loaded)
            return
        self.dur_lbl.config(text=T("ed_total", fmt_time(self.duration)))
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
            c.create_text(w // 2, h // 2, text=T("ed_wave_loading"),
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
            messagebox.showwarning(T("ed_bad_time_t"), T("ed_bad_time"),
                                   parent=self)
            return
        a = min(max(0.0, a), self.duration)
        b = min(max(0.0, b), self.duration)
        if b <= a:
            messagebox.showwarning(T("ed_bad_range_t"), T("ed_bad_range"),
                                   parent=self)
            return
        self.sel = (a, b)
        self._render()

    # ------------------------------------------------------- 試聽
    def _preview(self):
        if not self.sel:
            messagebox.showinfo(T("ed_no_sel_t"), T("ed_no_sel"), parent=self)
            return
        if shutil.which("ffplay") is None:
            messagebox.showinfo(T("ed_no_ffplay_t"), T("ed_no_ffplay"),
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
            messagebox.showinfo(T("ed_no_sel_t"), T("ed_no_sel"), parent=self)
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
                tk.END, T("ed_item", i, fmt_time(a), fmt_time(b), b - a))
            total += b - a
        self.total_removed_lbl.config(
            text=T("ed_total_removed", total) if self.removed else "")
        self._render()

    # ------------------------------------------------------- 輸出
    def _export(self):
        if not self.removed:
            messagebox.showinfo(T("ed_no_seg_t"), T("ed_no_seg"), parent=self)
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
        self.app._start_cmds([cmd], T("ed_export_title"))

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
