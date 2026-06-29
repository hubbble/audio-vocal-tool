#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
音訊處理工具 — 拖放式圖形介面 (GUI)

功能：
  - 把檔案 / 影片直接拖進視窗（或用「加入檔案」按鈕）
  - 選擇操作：影片轉音檔 / 人聲分離 / 去靜音 / 人聲分離+去靜音 / 合併成MP3
  - 選擇運算裝置：auto / cuda(GPU) / cpu
  - 即時顯示處理進度（含 Demucs 輸出）

執行（用 venv 裡的 python，才有 torch / demucs）：
  .venv312\Scripts\python.exe gui.py

底層是呼叫 audio_tool.py，因此所有功能與 CLI 一致。
"""

import os
import queue
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

# 操作定義：顯示名稱 -> (CLI 操作, 是否需要輸出資料夾, 是否為合併)
OPERATIONS = {
    "影片轉音檔 (extract)":            ("extract", "dir", False),
    "人聲分離 (vocals)":               ("vocals", "dir", False),
    "去除靜音 (trim)":                 ("trim", "dir", False),
    "人聲分離 + 去靜音 (pipeline)":    ("pipeline", "dir", False),
    "合併成一個 MP3 (merge)":          ("merge", "file", True),
}


class App:
    def __init__(self, root):
        self.root = root
        root.title("音訊處理工具")
        root.geometry("720x560")
        self.files = []
        self.queue = queue.Queue()
        self.proc = None

        self._build_ui()
        self.root.after(100, self._drain_queue)

    # ----------------------------------------------------------------- UI
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # --- 檔案清單區 ---
        top = ttk.LabelFrame(self.root, text="輸入檔案（可直接拖曳進來）")
        top.pack(fill="both", expand=False, **pad)

        self.listbox = tk.Listbox(top, height=7, selectmode=tk.EXTENDED)
        self.listbox.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        sb = ttk.Scrollbar(top, command=self.listbox.yview)
        sb.pack(side="left", fill="y")
        self.listbox.config(yscrollcommand=sb.set)

        if _DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self._on_drop)

        btns = ttk.Frame(top)
        btns.pack(side="left", fill="y", padx=6)
        ttk.Button(btns, text="加入檔案", command=self._add_files).pack(fill="x", pady=2)
        ttk.Button(btns, text="加入資料夾", command=self._add_folder).pack(fill="x", pady=2)
        ttk.Button(btns, text="移除選取", command=self._remove_sel).pack(fill="x", pady=2)
        ttk.Button(btns, text="清空", command=self._clear).pack(fill="x", pady=2)

        # --- 設定區 ---
        opt = ttk.Frame(self.root)
        opt.pack(fill="x", **pad)

        ttk.Label(opt, text="操作：").grid(row=0, column=0, sticky="w")
        self.op_var = tk.StringVar(value=list(OPERATIONS)[3])
        op_menu = ttk.Combobox(opt, textvariable=self.op_var, state="readonly",
                               values=list(OPERATIONS), width=28)
        op_menu.grid(row=0, column=1, sticky="w", padx=6)

        ttk.Label(opt, text="裝置：").grid(row=0, column=2, sticky="w", padx=(16, 0))
        self.dev_var = tk.StringVar(value="auto")
        ttk.Combobox(opt, textvariable=self.dev_var, state="readonly",
                     values=["auto", "cuda", "cpu"], width=8).grid(
            row=0, column=3, sticky="w", padx=6)

        # 輸出
        out = ttk.Frame(self.root)
        out.pack(fill="x", **pad)
        ttk.Label(out, text="輸出位置：").pack(side="left")
        self.out_var = tk.StringVar(value=str(HERE / "output"))
        ttk.Entry(out, textvariable=self.out_var).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(out, text="選擇…", command=self._choose_output).pack(side="left")

        # --- 執行 ---
        run = ttk.Frame(self.root)
        run.pack(fill="x", **pad)
        self.run_btn = ttk.Button(run, text="開始處理", command=self._run)
        self.run_btn.pack(side="left")
        self.status = ttk.Label(run, text="就緒")
        self.status.pack(side="left", padx=12)
        if not _DND:
            ttk.Label(run, text="(未安裝 tkinterdnd2，請用按鈕加入檔案)",
                      foreground="#a60").pack(side="right")

        # --- 日誌 ---
        logf = ttk.LabelFrame(self.root, text="處理進度")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=12, wrap="word", state="disabled",
                           bg="#111", fg="#0f0", font=("Consolas", 9))
        self.log.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        lsb = ttk.Scrollbar(logf, command=self.log.yview)
        lsb.pack(side="left", fill="y")
        self.log.config(yscrollcommand=lsb.set)

    # ------------------------------------------------------------- 檔案操作
    def _on_drop(self, event):
        # tkdnd 用 {} 包住含空白的路徑，splitlist 可正確拆解
        for p in self.root.tk.splitlist(event.data):
            self._add_path(p)

    def _add_path(self, p):
        p = str(Path(p))
        if os.path.isfile(p) and p not in self.files:
            self.files.append(p)
            self.listbox.insert(tk.END, p)

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="選擇音訊 / 影片檔",
            filetypes=[("媒體檔", "*.mp3 *.wav *.flac *.m4a *.aac *.ogg "
                                  "*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv"),
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

    def _clear(self):
        self.files.clear()
        self.listbox.delete(0, tk.END)

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
        if self.proc is not None:
            messagebox.showinfo("處理中", "目前已有工作在執行。")
            return

        op, kind, is_merge = OPERATIONS[self.op_var.get()]
        out = self.out_var.get().strip()
        if not out:
            messagebox.showwarning("缺少輸出", "請設定輸出位置。")
            return

        # 組命令
        cmds = []  # 每個元素是一條完整命令 (list)
        py = sys.executable
        base = [py, str(AUDIO_TOOL)]

        if is_merge:
            cmds.append(base + ["merge"] + self.files + ["-o", out])
        else:
            Path(out).mkdir(parents=True, exist_ok=True)
            for f in self.files:
                stem = Path(f).stem
                if op == "extract":
                    cmds.append(base + ["extract", f, "-o",
                                        str(Path(out) / (stem + ".mp3"))])
                elif op == "vocals":
                    cmds.append(base + ["vocals", f, "-o", out,
                                        "-d", self.dev_var.get()])
                elif op == "trim":
                    cmds.append(base + ["trim", f, "-o",
                                        str(Path(out) / (stem + "_clean.mp3"))])
                elif op == "pipeline":
                    cmds.append(base + ["pipeline", f, "-o",
                                        str(Path(out) / (stem + "_vocal_clean.mp3")),
                                        "-d", self.dev_var.get()])

        self.run_btn.config(state="disabled")
        self.status.config(text="處理中…")
        self._log(f"\n===== 開始：{self.op_var.get()}（{len(cmds)} 項）=====\n")
        threading.Thread(target=self._worker, args=(cmds,), daemon=True).start()

    def _worker(self, cmds):
        # PYTHONUNBUFFERED=1：強制子程序即時輸出，否則 Demucs 進度條會卡在
        # 緩衝區裡，畫面看起來像當機。
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        ok = 0
        for i, cmd in enumerate(cmds, 1):
            # cmd = [python, audio_tool.py, <op>, <input...>, ...]
            shown = " ".join(Path(c).name if os.path.sep in c else c
                             for c in cmd[2:])
            self.queue.put(("log", f"\n--- [{i}/{len(cmds)}] {shown} ---\n"))
            try:
                self.proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    bufsize=1, env=env)
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
                        if buf.strip():
                            self.queue.put(("progress", buf.strip()))
                        buf = ""
                    else:
                        buf += ch
                if buf.strip():
                    self.queue.put(("log", buf + "\n"))
                self.proc.wait()
                if self.proc.returncode == 0:
                    ok += 1
                else:
                    self.queue.put(("log", f"[失敗] 回傳碼 {self.proc.returncode}\n"))
            except Exception as e:
                self.queue.put(("log", f"[錯誤] {e}\n"))
            finally:
                self.proc = None
        self.queue.put(("done", f"完成：成功 {ok}/{len(cmds)} 項"))

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "progress":
                    # 進度條：只更新狀態列，不洗版日誌
                    self.status.config(text=payload)
                elif kind == "done":
                    self._log(f"\n===== {payload} =====\n")
                    self.status.config(text=payload)
                    self.run_btn.config(state="normal")
                    messagebox.showinfo("完成", payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)


def main():
    if not AUDIO_TOOL.is_file():
        print(f"找不到 {AUDIO_TOOL}")
        sys.exit(1)
    root = TkinterDnD.Tk() if _DND else tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
