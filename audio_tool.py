#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音訊處理工具 (audio_tool)

功能：
  1. vocals  : 把人聲乾淨地分離出來，去除背景雜音與音樂 (使用 Demucs)
  2. trim    : 去除音檔中無聲 / 空白的片段
  3. merge   : 把多個音檔合併成一個，輸出成 MP3
  4. extract : 從影片抽出聲音
  5. convert : 音訊轉檔 (mp3/wav/flac/m4a/ogg/opus)
  6. cut     : 剪輯，把自選的時間片段剪掉
  7. split   : 把音檔分割成多段（依秒數或等分）
  8. normalize : 音量標準化（LUFS 響度 或 峰值）
  9. speakers  : 分離不同說話人（pyannote 語者分離，需 Hugging Face token）

也提供 pipeline，一次跑完「分離人聲 → 去靜音」整套流程。

用法範例：
  py audio_tool.py vocals  input.mp3 -o out/
  py audio_tool.py trim    input.wav -o clean.mp3
  py audio_tool.py merge   a.mp3 b.mp3 c.wav -o final.mp3
  py audio_tool.py convert input.wav -o output.mp3
  py audio_tool.py split   input.mp3 -o parts/ --seconds 300
  py audio_tool.py pipeline input.mp3 -o vocals_clean.mp3

需求：
  - Python 3.9+
  - 系統需安裝 ffmpeg 並可在 PATH 中執行
  - py -m pip install -r requirements.txt
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# 支援的副檔名
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv",
              ".m4v", ".wmv", ".ts", ".mpg", ".mpeg", ".3gp"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg",
              ".wma", ".opus", ".aiff", ".alac"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS


# --------------------------------------------------------------------------- #
# 共用工具
# --------------------------------------------------------------------------- #
def _check_ffmpeg() -> None:
    """確認系統有 ffmpeg，否則 pydub 無法處理 mp3。"""
    if shutil.which("ffmpeg") is None:
        sys.exit(
            "錯誤：找不到 ffmpeg。\n"
            "請先安裝 ffmpeg 並加入系統 PATH。\n"
            "Windows 可用：winget install Gyan.FFmpeg"
        )


def _import_pydub():
    try:
        from pydub import AudioSegment  # noqa
        from pydub.silence import detect_nonsilent  # noqa
        return AudioSegment, detect_nonsilent
    except ImportError:
        sys.exit("錯誤：缺少 pydub。請執行：py -m pip install pydub")


def _info(msg: str) -> None:
    print(f"[audio_tool] {msg}", flush=True)


# 從 GUI 用 pythonw.exe 啟動時，sys.executable 會是 pythonw.exe，
# 而「pythonw -m demucs」會卡死（無 console / stdin handle 無效）。
# 因此啟動子程序一律改用 console 版 python.exe。
def _python_exe() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.exists():
            return str(cand)
    return sys.executable


# Windows 下避免每個子程序彈出黑窗
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


# 各輸出格式對應的 ffmpeg 編碼參數（bitrate 只對有損格式有意義）
def _codec_args(ext: str, bitrate: str = "320k") -> list[str]:
    table = {
        ".mp3":  ["-c:a", "libmp3lame", "-b:a", bitrate],
        ".wav":  ["-c:a", "pcm_s16le"],
        ".flac": ["-c:a", "flac"],
        ".m4a":  ["-c:a", "aac", "-b:a", bitrate],
        ".aac":  ["-c:a", "aac", "-b:a", bitrate],
        ".ogg":  ["-c:a", "libvorbis", "-b:a", bitrate],
        ".opus": ["-c:a", "libopus", "-b:a", "128k"],
    }
    return table.get(ext)


SUPPORTED_OUT_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus")


def _media_duration(path) -> float:
    """用 ffprobe 取得媒體長度（秒），失敗回傳 0。"""
    if shutil.which("ffprobe") is None:
        return 0.0
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, creationflags=_NO_WINDOW)
    try:
        return float(r.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _parse_time(s: str) -> float:
    """把 '90'、'1:30'、'0:01:30.5' 這類時間字串轉成秒數。"""
    s = s.strip()
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        sys.exit(f"錯誤：看不懂的時間格式 {s!r}（可用 90、1:30、0:01:30.5）")
    if not 1 <= len(parts) <= 3:
        sys.exit(f"錯誤：看不懂的時間格式 {s!r}")
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def _parse_ranges(specs: list[str], total_ms: int) -> list[list[int]]:
    """把 '1:00-2:30' 之類的片段字串解析成毫秒區間，排序並合併重疊。"""
    ranges = []
    for spec in specs:
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" not in part:
                sys.exit(f"錯誤：片段格式應為 開始-結束（例如 1:00-2:30），"
                         f"收到 {part!r}")
            a, b = part.split("-", 1)
            s = int(_parse_time(a) * 1000)
            e = int(_parse_time(b) * 1000)
            if e <= s:
                sys.exit(f"錯誤：{part} 的結束時間必須大於開始時間。")
            s, e = max(0, s), min(e, total_ms)
            if e > s:
                ranges.append([s, e])
    ranges.sort()
    merged = []
    for s, e in ranges:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _export_audio(seg, out_path: Path, bitrate: str = "320k") -> None:
    """用 pydub 輸出音檔，格式由副檔名決定（處理 m4a/aac 的 muxer 名稱）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ext = out_path.suffix.lstrip(".").lower() or "mp3"
    fmt = {"m4a": "ipod", "aac": "adts"}.get(ext, ext)
    kwargs = {"bitrate": bitrate} if ext in ("mp3", "m4a", "aac", "ogg") else {}
    seg.export(out_path, format=fmt, **kwargs)


def detect_device(prefer: str = "auto") -> str:
    """
    決定 Demucs 要用的運算裝置。

    prefer = "auto" : 有可用 GPU 就用 GPU，否則 CPU
    prefer = "cuda" : 強制 GPU（AMD ROCm on Windows 也是透過 torch.cuda 介面）
    prefer = "cpu"  : 強制 CPU

    回傳給 Demucs -d 用的字串："cuda" 或 "cpu"。
    """
    if prefer == "cpu":
        return "cpu"

    try:
        import torch
    except ImportError:
        if prefer == "cuda":
            sys.exit("錯誤：指定了 --device cuda 但找不到 torch，請先安裝 PyTorch。")
        return "cpu"

    # ROCm 版 PyTorch（AMD GPU on Windows）同樣用 torch.cuda 介面回報可用性
    if torch.cuda.is_available():
        try:
            name = torch.cuda.get_device_name(0)
        except Exception:
            name = "GPU"
        _info(f"使用 GPU 加速：{name}")
        return "cuda"

    if prefer == "cuda":
        sys.exit(
            "錯誤：指定了 --device cuda，但 torch.cuda.is_available() 為 False。\n"
            "AMD 顯卡請確認已安裝 ROCm 版 PyTorch（見 setup_amd_gpu.ps1 / README）。"
        )
    _info("找不到可用 GPU，改用 CPU（速度較慢）。")
    return "cpu"


# --------------------------------------------------------------------------- #
# 功能 1：人聲分離（去背景雜音 / 音樂）
# --------------------------------------------------------------------------- #
def separate_vocals(
    input_path: str,
    output_dir: str,
    model: str = "htdemucs",
    mp3: bool = True,
    device: str = "auto",
) -> str:
    """
    使用 Demucs 把人聲從背景音樂 / 雜音中分離出來。

    回傳分離後「人聲」檔案的路徑。
    """
    in_path = Path(input_path)
    if not in_path.is_file():
        sys.exit(f"錯誤：找不到輸入檔 {in_path}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 確認 demucs 可用
    try:
        import demucs  # noqa
    except ImportError:
        sys.exit("錯誤：缺少 demucs。請執行：py -m pip install demucs")

    dev = detect_device(device)
    _info(f"開始用 Demucs ({model}, device={dev}) 分離人聲，"
          f"第一次會自動下載模型，請稍候…")

    # 注意：torchaudio 2.9 存 WAV 會走 torchcodec，與本機 ffmpeg 8 不相容，
    # 因此一律讓 Demucs 以 MP3 (lameenc) 輸出；若呼叫端要 WAV，再用 pydub 轉。
    # --two-stems=vocals 只切出「人聲」與「其餘」兩軌，速度較快也更乾淨
    cmd = [
        _python_exe(), "-m", "demucs",
        "-n", model,
        "--two-stems", "vocals",
        "-d", dev,
        "--mp3", "--mp3-bitrate", "320",
        "-o", str(out_dir),
        str(in_path),
    ]

    result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)
    if result.returncode != 0:
        sys.exit("錯誤：Demucs 執行失敗，請檢查上方訊息。")

    # Demucs 輸出路徑：<out_dir>/<model>/<檔名>/vocals.mp3
    stem_dir = out_dir / model / in_path.stem
    vocals_file = stem_dir / "vocals.mp3"
    if not vocals_file.is_file():
        candidates = list(stem_dir.glob("vocals.*"))
        if candidates:
            vocals_file = candidates[0]
        else:
            sys.exit(f"錯誤：找不到分離後的人聲檔，預期位置 {vocals_file}")

    # 呼叫端要 wav 的話，用 pydub 把 mp3 轉成 wav
    if not mp3:
        AudioSegment, _ = _import_pydub()
        wav_file = stem_dir / "vocals.wav"
        AudioSegment.from_file(vocals_file).export(wav_file, format="wav")
        vocals_file = wav_file

    _info(f"完成！人聲檔：{vocals_file}")
    _info(f"伴奏 / 背景檔同目錄 no_vocals.mp3")
    return str(vocals_file)


# --------------------------------------------------------------------------- #
# 功能 2：去除無聲 / 空白片段
# --------------------------------------------------------------------------- #
def trim_silence(
    input_path: str,
    output_path: str,
    silence_thresh: int = -40,
    min_silence_len: int = 500,
    keep_padding: int = 100,
) -> str:
    """
    去除音檔中的靜音片段，只保留有聲音的部分。

    參數：
      silence_thresh : 低於這個音量(dBFS)視為靜音，越小越嚴格 (預設 -40)
      min_silence_len: 連續靜音超過幾毫秒才會被移除 (預設 500ms)
      keep_padding   : 每段有聲片段前後各保留多少毫秒，避免切太死 (預設 100ms)
    """
    AudioSegment, detect_nonsilent = _import_pydub()
    _check_ffmpeg()

    in_path = Path(input_path)
    if not in_path.is_file():
        sys.exit(f"錯誤：找不到輸入檔 {in_path}")

    _info(f"讀取 {in_path} …")
    audio = AudioSegment.from_file(in_path)

    _info("偵測有聲片段…")
    nonsilent = detect_nonsilent(
        audio,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh,
    )

    if not nonsilent:
        sys.exit("警告：整段都被判定為靜音，請調高 --silence-thresh (例如 -50)。")

    # 把所有有聲片段(含 padding)接起來
    result = AudioSegment.empty()
    total_ms = len(audio)
    for start, end in nonsilent:
        s = max(0, start - keep_padding)
        e = min(total_ms, end + keep_padding)
        result += audio[s:e]

    removed = (total_ms - len(result)) / 1000.0
    _info(f"原長 {total_ms/1000:.1f}s → 去靜音後 {len(result)/1000:.1f}s "
          f"(移除約 {removed:.1f}s)")

    out_path = Path(output_path)
    _export_audio(result, out_path)

    _info(f"完成！輸出：{out_path}")
    return str(out_path)


# --------------------------------------------------------------------------- #
# 功能 3：合併多個音檔 → MP3
# --------------------------------------------------------------------------- #
def merge_audio(
    input_paths: list[str],
    output_path: str,
    crossfade: int = 0,
    bitrate: str = "320k",
) -> str:
    """
    依序把多個音檔串接成一個，輸出成 MP3。

    參數：
      crossfade : 相鄰兩段交叉淡入淡出的毫秒數 (預設 0 = 直接接)
    """
    AudioSegment, _ = _import_pydub()
    _check_ffmpeg()

    if len(input_paths) < 1:
        sys.exit("錯誤：至少要提供一個輸入檔。")

    combined = None
    for p in input_paths:
        path = Path(p)
        if not path.is_file():
            sys.exit(f"錯誤：找不到輸入檔 {path}")
        _info(f"加入 {path} …")
        seg = AudioSegment.from_file(path)
        if combined is None:
            combined = seg
        else:
            # crossfade 不能比任一段還長，否則 pydub 會直接丟例外
            cf = min(crossfade, len(combined), len(seg))
            combined = combined.append(seg, crossfade=cf)

    out_path = Path(output_path)
    if out_path.suffix.lower() != ".mp3":
        out_path = out_path.with_suffix(".mp3")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _info(f"輸出 MP3：{out_path} (bitrate={bitrate}) …")
    combined.export(out_path, format="mp3", bitrate=bitrate)

    _info(f"完成！總長 {len(combined)/1000:.1f}s → {out_path}")
    return str(out_path)


# --------------------------------------------------------------------------- #
# 功能 4：音訊轉檔（也可從影片抽出聲音）
# --------------------------------------------------------------------------- #
def convert_audio(
    input_path: str,
    output_path: str,
    bitrate: str = "320k",
) -> str:
    """
    把任何音檔 / 影片轉成指定的音訊格式，格式由 output_path 副檔名決定。

    支援輸出：mp3 / wav / flac / m4a / aac / ogg / opus
    """
    _check_ffmpeg()
    in_path = Path(input_path)
    if not in_path.is_file():
        sys.exit(f"錯誤：找不到輸入檔 {in_path}")

    out_path = Path(output_path)
    ext = out_path.suffix.lower()
    codec = _codec_args(ext, bitrate)
    if codec is None:
        sys.exit(f"錯誤：不支援的輸出格式 {ext or '(無副檔名)'}，"
                 f"支援：{' '.join(SUPPORTED_OUT_EXTS)}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = (["ffmpeg", "-y", "-loglevel", "error", "-stats",
            "-i", str(in_path), "-vn"] + codec + [str(out_path)])

    _info(f"轉檔 {in_path.name} → {out_path.name} …")
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)
    if result.returncode != 0:
        sys.exit("錯誤：ffmpeg 轉檔失敗。")

    _info(f"完成！輸出：{out_path}")
    return str(out_path)


def extract_audio(
    input_path: str,
    output_path: str,
    bitrate: str = "320k",
) -> str:
    """從影片（mp4 / mkv / mov…）抽出聲音；副檔名不支援時改輸出 mp3。"""
    out_path = Path(output_path)
    if out_path.suffix.lower() not in SUPPORTED_OUT_EXTS:
        out_path = out_path.with_suffix(".mp3")
    return convert_audio(input_path, str(out_path), bitrate=bitrate)


# --------------------------------------------------------------------------- #
# 功能 5：剪輯（移除自選片段）
# --------------------------------------------------------------------------- #
def cut_audio(
    input_path: str,
    output_path: str,
    remove: list[str],
) -> str:
    """
    把指定的時間片段從音檔中剪掉，其餘部分接起來輸出。

    remove 是片段字串清單，例如 ["0:30-1:00", "2:10-2:45.5"]，
    時間可用 秒 / 分:秒 / 時:分:秒。重疊的片段會自動合併。
    """
    AudioSegment, _ = _import_pydub()
    _check_ffmpeg()

    in_path = Path(input_path)
    if not in_path.is_file():
        sys.exit(f"錯誤：找不到輸入檔 {in_path}")

    _info(f"讀取 {in_path} …")
    audio = AudioSegment.from_file(in_path)
    total_ms = len(audio)

    merged = _parse_ranges(remove, total_ms)
    if not merged:
        sys.exit("錯誤：--remove 至少要指定一個片段，例如 --remove 1:00-2:30")

    result = AudioSegment.empty()
    pos = 0
    for s, e in merged:
        if s > pos:
            result += audio[pos:s]
        pos = e
    if pos < total_ms:
        result += audio[pos:total_ms]

    if len(result) == 0:
        sys.exit("錯誤：全部片段都被剪掉了，沒有東西可輸出。")

    removed_s = (total_ms - len(result)) / 1000.0
    _info(f"原長 {total_ms/1000:.1f}s，剪掉 {len(merged)} 段共 {removed_s:.1f}s "
          f"→ 剩 {len(result)/1000:.1f}s")

    out_path = Path(output_path)
    _export_audio(result, out_path)
    _info(f"完成！輸出：{out_path}")
    return str(out_path)


# --------------------------------------------------------------------------- #
# 功能 6：分割音檔
# --------------------------------------------------------------------------- #
def split_audio(
    input_path: str,
    output_dir: str,
    seconds: float = 0,
    parts: int = 0,
    fmt: str = "",
    bitrate: str = "320k",
) -> list[str]:
    """
    把音檔切成多段，輸出到資料夾（檔名為 原檔名_000、_001…）。

    參數：
      seconds : 每段長度（秒）
      parts   : 或改成「等分成幾段」（需要 ffprobe 取得總長）
      fmt     : 輸出格式（如 "mp3"），留空 = 與原檔相同
    """
    _check_ffmpeg()
    in_path = Path(input_path)
    if not in_path.is_file():
        sys.exit(f"錯誤：找不到輸入檔 {in_path}")
    if seconds <= 0 and parts <= 0:
        sys.exit("錯誤：--seconds 或 --parts 至少要指定一個。")

    if parts > 0:
        duration = _media_duration(in_path)
        if duration <= 0:
            sys.exit("錯誤：無法取得音檔長度（需要 ffprobe），請改用 --seconds。")
        # 加一點餘裕，避免浮點誤差多切出一小段
        seconds = duration / parts + 0.05

    src_ext = in_path.suffix.lower()
    ext = "." + fmt.lstrip(".").lower() if fmt else (
        src_ext if src_ext in SUPPORTED_OUT_EXTS else ".mp3")
    codec = _codec_args(ext, bitrate)
    if codec is None:
        sys.exit(f"錯誤：不支援的輸出格式 {ext}，支援：{' '.join(SUPPORTED_OUT_EXTS)}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / f"{in_path.stem}_%03d{ext}"

    # 格式不變的話直接串流複製，不重新編碼（快很多、無損）
    if ext == src_ext:
        codec = ["-c:a", "copy"]

    cmd = (["ffmpeg", "-y", "-loglevel", "error", "-stats",
            "-i", str(in_path), "-vn"] + codec +
           ["-f", "segment", "-segment_time", f"{seconds:g}",
            "-reset_timestamps", "1", str(pattern)])

    _info(f"分割 {in_path.name}（每段 {seconds:.0f} 秒）…")
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)
    if result.returncode != 0:
        sys.exit("錯誤：ffmpeg 分割失敗。")

    outputs = sorted(str(p) for p in out_dir.glob(f"{in_path.stem}_[0-9][0-9][0-9]{ext}"))
    _info(f"完成！共 {len(outputs)} 段，輸出在 {out_dir}")
    return outputs


# --------------------------------------------------------------------------- #
# 功能 7：音量標準化
# --------------------------------------------------------------------------- #
def _media_sample_rate(path) -> int:
    """用 ffprobe 取得取樣率，失敗回傳 44100。"""
    if shutil.which("ffprobe") is None:
        return 44100
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, creationflags=_NO_WINDOW)
    try:
        return int(r.stdout.strip())
    except (ValueError, AttributeError):
        return 44100


def normalize_audio(
    input_path: str,
    output_path: str,
    mode: str = "lufs",
    lufs: float = -16.0,
    tp: float = -1.5,
    peak_dbfs: float = -1.0,
    bitrate: str = "320k",
) -> str:
    """
    音量標準化。

    mode = "lufs"：EBU R128 響度標準化（兩段式 loudnorm，較精準），
                   lufs 是目標響度（串流平台常用 -14，podcast -16，廣播 -23），
                   tp 是真峰值上限 dBTP。
    mode = "peak"：簡單峰值標準化，把最大音量拉到 peak_dbfs（快，不改動態）。
    """
    _check_ffmpeg()
    in_path = Path(input_path)
    if not in_path.is_file():
        sys.exit(f"錯誤：找不到輸入檔 {in_path}")

    out_path = Path(output_path)
    ext = out_path.suffix.lower()
    codec = _codec_args(ext, bitrate)
    if codec is None:
        sys.exit(f"錯誤：不支援的輸出格式 {ext or '(無副檔名)'}，"
                 f"支援：{' '.join(SUPPORTED_OUT_EXTS)}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # --- 峰值模式：pydub 直接加增益 ---
    if mode == "peak":
        AudioSegment, _ = _import_pydub()
        audio = AudioSegment.from_file(in_path)
        peak = audio.max_dBFS
        if peak == float("-inf"):
            sys.exit("錯誤：整段都是靜音，無法做峰值標準化。")
        gain = peak_dbfs - peak
        _info(f"峰值 {peak:.1f} dBFS → 目標 {peak_dbfs:.1f} dBFS"
              f"（增益 {gain:+.1f} dB）")
        _export_audio(audio.apply_gain(gain), out_path, bitrate)
        _info(f"完成！輸出：{out_path}")
        return str(out_path)

    # --- LUFS 模式：ffmpeg loudnorm 兩段式 ---
    base_filter = f"loudnorm=I={lufs}:TP={tp}:LRA=11"
    _info(f"第 1 步：量測響度（目標 {lufs} LUFS）…")
    measure = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(in_path),
         "-af", base_filter + ":print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW)

    filter_str = base_filter
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", measure.stderr, re.S)
    if measure.returncode == 0 and m:
        try:
            stats = json.loads(m.group(0))
            _info(f"量測結果：{stats['input_i']} LUFS，"
                  f"真峰值 {stats['input_tp']} dBTP")
            filter_str = (
                base_filter
                + f":measured_I={stats['input_i']}"
                + f":measured_TP={stats['input_tp']}"
                + f":measured_LRA={stats['input_lra']}"
                + f":measured_thresh={stats['input_thresh']}"
                + f":offset={stats['target_offset']}"
                + ":linear=true"
            )
        except (json.JSONDecodeError, KeyError):
            _info("量測結果解析失敗，改用單段式 loudnorm。")
    else:
        _info("量測失敗，改用單段式 loudnorm。")

    # loudnorm 內部會升到 192kHz，輸出時降回原取樣率
    sr = _media_sample_rate(in_path)
    cmd = (["ffmpeg", "-y", "-loglevel", "error", "-stats",
            "-i", str(in_path), "-vn", "-af", filter_str, "-ar", str(sr)]
           + codec + [str(out_path)])

    _info("第 2 步：套用標準化並輸出…")
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)
    if result.returncode != 0:
        sys.exit("錯誤：ffmpeg 標準化失敗。")

    _info(f"完成！輸出：{out_path}")
    return str(out_path)


# --------------------------------------------------------------------------- #
# 功能 8：分離不同說話人（語者分離）
# --------------------------------------------------------------------------- #
_DIAR_MODEL = "pyannote/speaker-diarization-community-1"

_HF_HELP = (
    "分離說話人需要 Hugging Face token（免費）：\n"
    "  1. 到 https://huggingface.co 註冊帳號\n"
    "  2. 到下面模型頁按「Agree and access repository」接受條款：\n"
    f"       https://huggingface.co/{_DIAR_MODEL}\n"
    "  3. 到 https://huggingface.co/settings/tokens 建立 Read token\n"
    "  4. 把 token 存成本專案資料夾裡的 .hf_token 檔（純文字一行），\n"
    "     或設定環境變數 HF_TOKEN，或用 --token 參數傳入。"
)


def _hf_token(explicit: str = "") -> str:
    """依序找 HF token：--token 參數 > 環境變數 > .hf_token 檔。"""
    if explicit:
        return explicit
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    f = Path(__file__).resolve().parent / ".hf_token"
    if f.is_file():
        return f.read_text(encoding="utf-8").strip()
    return ""


def _subtract_intervals(base, cuts):
    """從 base 區間清單中扣掉 cuts 區間（毫秒整數），回傳剩餘片段。"""
    out = []
    for s, e in base:
        pieces = [(s, e)]
        for cs, ce in cuts:
            nxt = []
            for ps, pe in pieces:
                if ce <= ps or cs >= pe:
                    nxt.append((ps, pe))
                    continue
                if cs > ps:
                    nxt.append((ps, cs))
                if ce < pe:
                    nxt.append((ce, pe))
            pieces = nxt
        out.extend(pieces)
    return out


def _merge_intervals(ivs):
    """合併重疊的 (start, end) 區間清單。"""
    merged = []
    for s, e in sorted(ivs):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def separate_speakers(
    input_path: str,
    output_dir: str,
    num_speakers: int = 2,
    device: str = "auto",
    token: str = "",
    keep_overlap: bool = False,
    min_segment: int = 200,
    bitrate: str = "320k",
) -> list[str]:
    """
    用 pyannote 語者分離，把不同說話人的段落切開，各自輸出成一個音檔。

    參數：
      num_speakers : 說話人數（0 = 自動偵測）
      keep_overlap : 預設 False = 兩人同時說話的重疊片段直接捨棄
      min_segment  : 短於這個毫秒數的碎片不輸出（預設 200ms）
    """
    AudioSegment, _ = _import_pydub()
    _check_ffmpeg()

    in_path = Path(input_path)
    if not in_path.is_file():
        sys.exit(f"錯誤：找不到輸入檔 {in_path}")

    tok = _hf_token(token)
    if not tok:
        sys.exit("錯誤：找不到 Hugging Face token。\n\n" + _HF_HELP)

    try:
        import warnings
        with warnings.catch_warnings():
            # pyannote 匯入時會對 torchcodec 不可用發出長篇警告；
            # 我們用 pydub 解碼、以 waveform dict 傳入，不需要 torchcodec。
            warnings.simplefilter("ignore")
            import torch
            from pyannote.audio import Pipeline
    except ImportError:
        sys.exit("錯誤：缺少 pyannote.audio。請執行：\n"
                 "  .venv312\\Scripts\\python.exe -m pip install pyannote.audio")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _info("載入 pyannote 語者分離模型（第一次會下載，請稍候）…")
    try:
        pipe = Pipeline.from_pretrained(_DIAR_MODEL, token=tok)
    except TypeError:
        # 舊版 pyannote 用 use_auth_token 參數
        pipe = Pipeline.from_pretrained(_DIAR_MODEL, use_auth_token=tok)
    except Exception as e:
        sys.exit(f"錯誤：模型載入失敗：{e}\n\n"
                 f"最常見原因是還沒接受模型使用條款或 token 無效。\n\n{_HF_HELP}")
    if pipe is None:
        sys.exit("錯誤：模型載入失敗（回傳 None），通常是還沒在 Hugging Face "
                 "頁面接受使用條款。\n\n" + _HF_HELP)

    dev = detect_device(device)
    pipe.to(torch.device(dev))

    _info(f"讀取並解碼 {in_path.name} …")
    audio = AudioSegment.from_file(in_path)
    total_ms = len(audio)
    # pyannote 吃 16kHz 單聲道；用 pydub 解碼再轉 tensor，
    # 完全繞過 torchaudio/torchcodec（在 ROCm 環境不可用）
    mono16 = audio.set_channels(1).set_frame_rate(16000).set_sample_width(2)
    waveform = torch.frombuffer(
        bytearray(mono16.raw_data), dtype=torch.int16
    ).float().unsqueeze(0) / 32768.0

    _info("分析說話人（這步最花時間）…")

    def hook(name, *args, total=None, completed=None, **kwargs):
        if total:
            print(f"\r{name} {completed / total * 100:.0f}%",
                  end="", flush=True)

    def _run_diar():
        kw = {"hook": hook}
        if num_speakers and num_speakers > 0:
            kw["num_speakers"] = num_speakers
        return pipe({"waveform": waveform, "sample_rate": 16000}, **kw)

    try:
        result = _run_diar()
    except RuntimeError as e:
        # ROCm on Windows 的 MIOpen 對 InstanceNorm 有已知 bug
        # (miopenStatusUnknownError)，先停用 MIOpen 用 PyTorch 原生
        # 核心重試（仍走 GPU），不行再退回 CPU。
        if dev != "cuda" or "miopen" not in str(e).lower():
            raise
        print(flush=True)
        _info("GPU 的 MIOpen 出錯（ROCm on Windows 已知問題），"
              "停用 MIOpen 後重試…")
        torch.backends.cudnn.enabled = False
        try:
            result = _run_diar()
        except RuntimeError:
            print(flush=True)
            _info("GPU 仍然失敗，改用 CPU 重跑（較慢但穩定）…")
            pipe.to(torch.device("cpu"))
            result = _run_diar()
    print(flush=True)
    # pyannote 4.x 回傳 DiarizeOutput 物件，3.x 直接回傳 Annotation
    diar = getattr(result, "speaker_diarization", result)

    # 整理成 說話人 -> [(start_ms, end_ms)]
    by_spk: dict[str, list] = {}
    for turn, _track, label in diar.itertracks(yield_label=True):
        s = max(0, int(turn.start * 1000))
        e = min(total_ms, int(turn.end * 1000))
        if e > s:
            by_spk.setdefault(label, []).append((s, e))
    if not by_spk:
        sys.exit("錯誤：沒有偵測到任何說話人。")

    labels = sorted(by_spk)
    _info(f"偵測到 {len(labels)} 位說話人。")

    # 找出「兩人以上同時說話」的重疊區，預設直接捨棄
    overlaps = []
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            for s1, e1 in by_spk[a]:
                for s2, e2 in by_spk[b]:
                    s, e = max(s1, s2), min(e1, e2)
                    if e > s:
                        overlaps.append((s, e))
    overlaps = _merge_intervals(overlaps)
    overlap_ms = sum(e - s for s, e in overlaps)
    if overlaps and not keep_overlap:
        _info(f"重疊片段共 {overlap_ms / 1000:.1f}s，依設定捨棄。")

    ext = in_path.suffix.lower()
    if ext not in SUPPORTED_OUT_EXTS:
        ext = ".mp3"

    outputs = []
    for i, label in enumerate(labels, 1):
        segs = _merge_intervals(by_spk[label])
        if overlaps and not keep_overlap:
            segs = _subtract_intervals(segs, overlaps)
        segs = [(s, e) for s, e in sorted(segs) if e - s >= min_segment]
        if not segs:
            _info(f"說話人 {i}（{label}）扣掉重疊後沒有可輸出的片段，跳過。")
            continue
        result = AudioSegment.empty()
        for s, e in segs:
            result += audio[s:e]
        out_path = out_dir / f"{in_path.stem}_speaker{i}{ext}"
        _export_audio(result, out_path, bitrate)
        _info(f"說話人 {i}（{label}）：{len(segs)} 段，"
              f"共 {len(result) / 1000:.1f}s → {out_path.name}")
        outputs.append(str(out_path))

    _info(f"完成！輸出在 {out_dir}")
    return outputs


# --------------------------------------------------------------------------- #
# 功能 9：批次處理整個資料夾
# --------------------------------------------------------------------------- #
def batch_process(
    input_dir: str,
    output_dir: str,
    op: str = "pipeline",
    device: str = "auto",
    recursive: bool = False,
    silence_thresh: int = -40,
    min_silence_len: int = 500,
    model: str = "htdemucs",
) -> list[str]:
    """
    對資料夾內所有音/視訊檔執行指定操作。

    op:
      extract  : 影片轉音檔 (mp3)
      vocals   : 人聲分離 (輸出資料夾)
      trim     : 去靜音 (mp3)
      pipeline : 分離人聲 → 去靜音 (mp3)
    """
    in_dir = Path(input_dir)
    if not in_dir.is_dir():
        sys.exit(f"錯誤：找不到資料夾 {in_dir}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pattern = "**/*" if recursive else "*"
    # extract 只找影片，其餘找所有媒體
    exts = VIDEO_EXTS if op == "extract" else MEDIA_EXTS
    files = sorted(p for p in in_dir.glob(pattern)
                   if p.is_file() and p.suffix.lower() in exts)

    if not files:
        sys.exit(f"在 {in_dir} 找不到可處理的檔案（{op}）。")

    _info(f"找到 {len(files)} 個檔案，開始批次 {op} …")
    outputs = []
    for i, f in enumerate(files, 1):
        _info(f"--- [{i}/{len(files)}] {f.name} ---")
        try:
            if op == "extract":
                out = extract_audio(str(f), str(out_dir / (f.stem + ".mp3")))
            elif op == "vocals":
                out = separate_vocals(str(f), str(out_dir),
                                      model=model, device=device)
            elif op == "trim":
                out = trim_silence(str(f), str(out_dir / (f.stem + "_clean.mp3")),
                                   silence_thresh=silence_thresh,
                                   min_silence_len=min_silence_len)
            elif op == "pipeline":
                out = pipeline(str(f), str(out_dir / (f.stem + "_vocal_clean.mp3")),
                               model=model, device=device,
                               silence_thresh=silence_thresh,
                               min_silence_len=min_silence_len)
            else:
                sys.exit(f"錯誤：不支援的批次操作 {op}")
            outputs.append(out)
        except SystemExit as e:
            # 單一檔失敗不中斷整批
            _info(f"  ⚠ 跳過 {f.name}：{e}")

    _info(f"批次完成，成功 {len(outputs)}/{len(files)} 個。輸出在 {out_dir}")
    return outputs


# --------------------------------------------------------------------------- #
# 整合流程：分離人聲 → 去靜音
# --------------------------------------------------------------------------- #
def pipeline(input_path: str, output_path: str, model: str = "htdemucs",
             silence_thresh: int = -40, min_silence_len: int = 500,
             device: str = "auto") -> str:
    """一次跑完：分離乾淨人聲 → 去除靜音，輸出成單一檔案。"""
    with tempfile.TemporaryDirectory() as tmp:
        vocals = separate_vocals(input_path, tmp, model=model, mp3=True,
                                 device=device)
        return trim_silence(
            vocals, output_path,
            silence_thresh=silence_thresh,
            min_silence_len=min_silence_len,
        )


# --------------------------------------------------------------------------- #
# GPU 檢查
# --------------------------------------------------------------------------- #
def _gpu_report() -> None:
    """印出 PyTorch / GPU 狀態，方便確認 AMD ROCm 是否生效。"""
    try:
        import torch
    except ImportError:
        print("PyTorch 尚未安裝。")
        print("AMD 顯卡請執行 setup_amd_gpu.ps1 安裝 ROCm 版 PyTorch。")
        return

    print(f"torch 版本      : {torch.__version__}")
    hip = getattr(torch.version, "hip", None)
    cuda = getattr(torch.version, "cuda", None)
    print(f"ROCm/HIP 版本   : {hip}")
    print(f"CUDA 版本       : {cuda}")
    avail = torch.cuda.is_available()
    print(f"GPU 可用        : {avail}")
    if avail:
        for i in range(torch.cuda.device_count()):
            print(f"  [{i}] {torch.cuda.get_device_name(i)}")
        print("→ Demucs 會用 -d cuda 走 GPU 加速。")
    else:
        print("→ 目前只能用 CPU。AMD 顯卡請確認已照 setup_amd_gpu.ps1 安裝 ROCm 版 PyTorch，")
        print("  且顯卡驅動為 AMD 要求的版本 (PyTorch on Windows 7.2.1 需 26.2.2 以上)。")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="音訊處理工具：人聲分離 / 去靜音 / 合併成 MP3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # vocals
    p_v = sub.add_parser("vocals", help="分離人聲，去除背景雜音與音樂")
    p_v.add_argument("input", help="輸入音檔")
    p_v.add_argument("-o", "--output", default="separated",
                     help="輸出資料夾 (預設 separated/)")
    p_v.add_argument("-m", "--model", default="htdemucs",
                     help="Demucs 模型名稱 (預設 htdemucs)")
    p_v.add_argument("--wav", action="store_true",
                     help="輸出 wav 而非 mp3")
    p_v.add_argument("-d", "--device", default="auto",
                     choices=["auto", "cuda", "cpu"],
                     help="運算裝置：auto 自動偵測 / cuda 用 GPU(含AMD ROCm) / cpu")

    # trim
    p_t = sub.add_parser("trim", help="去除無聲 / 空白片段")
    p_t.add_argument("input", help="輸入音檔")
    p_t.add_argument("-o", "--output", required=True,
                     help="輸出檔 (副檔名決定格式，例如 clean.mp3)")
    p_t.add_argument("--silence-thresh", type=int, default=-40,
                     help="靜音門檻 dBFS，越小越嚴格 (預設 -40)")
    p_t.add_argument("--min-silence-len", type=int, default=500,
                     help="連續靜音幾毫秒才移除 (預設 500)")
    p_t.add_argument("--keep-padding", type=int, default=100,
                     help="每段前後保留毫秒數 (預設 100)")

    # merge
    p_m = sub.add_parser("merge", help="合併多個音檔成一個 MP3")
    p_m.add_argument("inputs", nargs="+", help="多個輸入音檔 (依順序)")
    p_m.add_argument("-o", "--output", required=True, help="輸出 MP3 路徑")
    p_m.add_argument("--crossfade", type=int, default=0,
                     help="相鄰段落交叉淡化毫秒數 (預設 0)")
    p_m.add_argument("--bitrate", default="320k", help="MP3 位元率 (預設 320k)")

    # pipeline
    p_p = sub.add_parser("pipeline", help="分離人聲 → 去靜音 一次完成")
    p_p.add_argument("input", help="輸入音檔")
    p_p.add_argument("-o", "--output", required=True, help="輸出檔 (例如 out.mp3)")
    p_p.add_argument("-m", "--model", default="htdemucs", help="Demucs 模型")
    p_p.add_argument("--silence-thresh", type=int, default=-40)
    p_p.add_argument("--min-silence-len", type=int, default=500)
    p_p.add_argument("-d", "--device", default="auto",
                     choices=["auto", "cuda", "cpu"],
                     help="運算裝置：auto / cuda / cpu")

    # extract
    p_e = sub.add_parser("extract", help="影片轉音檔（抽出聲音）")
    p_e.add_argument("input", help="輸入影片檔")
    p_e.add_argument("-o", "--output", required=True,
                     help="輸出音檔 (.mp3 或 .wav)")
    p_e.add_argument("--bitrate", default="320k", help="MP3 位元率 (預設 320k)")

    # convert
    p_c = sub.add_parser("convert", help="音訊轉檔 (mp3/wav/flac/m4a/ogg/opus)")
    p_c.add_argument("input", help="輸入音檔或影片")
    p_c.add_argument("-o", "--output", required=True,
                     help="輸出檔，副檔名決定格式 (例如 out.flac)")
    p_c.add_argument("--bitrate", default="320k", help="有損格式位元率 (預設 320k)")

    # cut
    p_x = sub.add_parser("cut", help="剪輯：把自選的時間片段剪掉")
    p_x.add_argument("input", help="輸入音檔")
    p_x.add_argument("-o", "--output", required=True,
                     help="輸出檔 (副檔名決定格式，例如 cut.mp3)")
    p_x.add_argument("--remove", action="append", required=True,
                     metavar="START-END",
                     help="要剪掉的片段，可重複指定或用逗號分隔多段，"
                          "例如 --remove 0:30-1:00 --remove 2:10-2:45.5")

    # split
    p_s = sub.add_parser("split", help="分割音檔成多段")
    p_s.add_argument("input", help="輸入音檔")
    p_s.add_argument("-o", "--output", required=True, help="輸出資料夾")
    p_s.add_argument("--seconds", type=float, default=0,
                     help="每段長度（秒）")
    p_s.add_argument("--parts", type=int, default=0,
                     help="或等分成幾段（與 --seconds 擇一）")
    p_s.add_argument("--fmt", default="",
                     help="輸出格式（如 mp3），預設與原檔相同")
    p_s.add_argument("--bitrate", default="320k", help="有損格式位元率 (預設 320k)")

    # batch
    p_b = sub.add_parser("batch", help="批次處理整個資料夾")
    p_b.add_argument("input_dir", help="輸入資料夾")
    p_b.add_argument("-o", "--output", required=True, help="輸出資料夾")
    p_b.add_argument("--op", default="pipeline",
                     choices=["extract", "vocals", "trim", "pipeline"],
                     help="要執行的操作 (預設 pipeline)")
    p_b.add_argument("-d", "--device", default="auto",
                     choices=["auto", "cuda", "cpu"], help="運算裝置")
    p_b.add_argument("-r", "--recursive", action="store_true",
                     help="連同子資料夾一起處理")
    p_b.add_argument("--silence-thresh", type=int, default=-40)
    p_b.add_argument("--min-silence-len", type=int, default=500)
    p_b.add_argument("-m", "--model", default="htdemucs", help="Demucs 模型")

    # normalize
    p_n = sub.add_parser("normalize", help="音量標準化（LUFS 響度 或 峰值）")
    p_n.add_argument("input", help="輸入音檔")
    p_n.add_argument("-o", "--output", required=True,
                     help="輸出檔 (副檔名決定格式)")
    p_n.add_argument("--mode", default="lufs", choices=["lufs", "peak"],
                     help="lufs=響度標準化(預設) / peak=峰值標準化")
    p_n.add_argument("--lufs", type=float, default=-16.0,
                     help="目標響度 LUFS：串流 -14 / 通用 -16 / 廣播 -23 (預設 -16)")
    p_n.add_argument("--tp", type=float, default=-1.5,
                     help="真峰值上限 dBTP (預設 -1.5)")
    p_n.add_argument("--peak-dbfs", type=float, default=-1.0,
                     help="peak 模式的目標峰值 dBFS (預設 -1.0)")
    p_n.add_argument("--bitrate", default="320k", help="有損格式位元率 (預設 320k)")

    # speakers
    p_k = sub.add_parser("speakers", help="分離不同說話人（需 Hugging Face token）")
    p_k.add_argument("input", help="輸入音檔")
    p_k.add_argument("-o", "--output", required=True, help="輸出資料夾")
    p_k.add_argument("--speakers", type=int, default=2,
                     help="說話人數，0 = 自動偵測 (預設 2)")
    p_k.add_argument("-d", "--device", default="auto",
                     choices=["auto", "cuda", "cpu"], help="運算裝置")
    p_k.add_argument("--token", default="",
                     help="Hugging Face token（也可用 HF_TOKEN 環境變數或 .hf_token 檔）")
    p_k.add_argument("--keep-overlap", action="store_true",
                     help="保留兩人同時說話的重疊片段（預設捨棄）")
    p_k.add_argument("--min-segment", type=int, default=200,
                     help="短於這個毫秒數的碎片不輸出 (預設 200)")
    p_k.add_argument("--bitrate", default="320k", help="有損格式位元率 (預設 320k)")

    # gpu
    sub.add_parser("gpu", help="檢查 GPU / PyTorch 是否可用")

    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)

    if args.command == "gpu":
        _gpu_report()
    elif args.command == "vocals":
        separate_vocals(args.input, args.output, model=args.model,
                        mp3=not args.wav, device=args.device)
    elif args.command == "trim":
        trim_silence(args.input, args.output,
                     silence_thresh=args.silence_thresh,
                     min_silence_len=args.min_silence_len,
                     keep_padding=args.keep_padding)
    elif args.command == "merge":
        merge_audio(args.inputs, args.output,
                    crossfade=args.crossfade, bitrate=args.bitrate)
    elif args.command == "pipeline":
        pipeline(args.input, args.output, model=args.model,
                 silence_thresh=args.silence_thresh,
                 min_silence_len=args.min_silence_len,
                 device=args.device)
    elif args.command == "extract":
        extract_audio(args.input, args.output, bitrate=args.bitrate)
    elif args.command == "convert":
        convert_audio(args.input, args.output, bitrate=args.bitrate)
    elif args.command == "cut":
        cut_audio(args.input, args.output, remove=args.remove)
    elif args.command == "normalize":
        normalize_audio(args.input, args.output, mode=args.mode,
                        lufs=args.lufs, tp=args.tp,
                        peak_dbfs=args.peak_dbfs, bitrate=args.bitrate)
    elif args.command == "speakers":
        separate_speakers(args.input, args.output,
                          num_speakers=args.speakers, device=args.device,
                          token=args.token, keep_overlap=args.keep_overlap,
                          min_segment=args.min_segment, bitrate=args.bitrate)
    elif args.command == "split":
        split_audio(args.input, args.output, seconds=args.seconds,
                    parts=args.parts, fmt=args.fmt, bitrate=args.bitrate)
    elif args.command == "batch":
        batch_process(args.input_dir, args.output, op=args.op,
                      device=args.device, recursive=args.recursive,
                      silence_thresh=args.silence_thresh,
                      min_silence_len=args.min_silence_len,
                      model=args.model)


if __name__ == "__main__":
    main()
