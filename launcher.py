#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
輕量啟動器：被打包成 exe 後，雙擊即用 .venv312 的 pythonw 開啟 gui.py。
exe 本身不含 torch，所以體積很小；GPU 加速由 venv 環境提供。

打包：
  .venv312\\Scripts\\python.exe -m PyInstaller --onefile --windowed --name 音訊處理工具 launcher.py

注意：產生的 exe 必須放在專案資料夾（與 .venv312、gui.py 同層）才能正確找到環境。
"""

import os
import subprocess
import sys
from pathlib import Path


def _base_dir() -> Path:
    # 被 PyInstaller 凍結後，exe 路徑在 sys.executable
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _error_box(msg: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "音訊處理工具", 0x10)
    except Exception:
        print(msg)


def main() -> int:
    base = _base_dir()
    pyw = base / ".venv312" / "Scripts" / "pythonw.exe"
    gui = base / "gui.py"

    if not pyw.is_file() or not gui.is_file():
        _error_box(
            "找不到執行環境：\n"
            f"{pyw}\n或\n{gui}\n\n"
            "請把這個 exe 放在專案資料夾裡（要和 .venv312、gui.py 在同一層）。"
        )
        return 1

    # 用 venv 的 pythonw 啟動 GUI（不彈黑窗），工作目錄設在專案資料夾
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.Popen([str(pyw), str(gui)], cwd=str(base),
                     creationflags=creationflags)
    return 0


if __name__ == "__main__":
    sys.exit(main())
