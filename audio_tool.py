#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音訊處理工具 (audio_tool)

三大功能：
  1. vocals  : 把人聲乾淨地分離出來，去除背景雜音與音樂 (使用 Demucs)
  2. trim    : 去除音檔中無聲 / 空白的片段
  3. merge   : 把多個音檔合併成一個，輸出成 MP3

也提供 pipeline，一次跑完「分離人聲 → 去靜音」整套流程。

用法範例：
  py audio_tool.py vocals  input.mp3 -o out/
  py audio_tool.py trim    input.wav -o clean.mp3
  py audio_tool.py merge   a.mp3 b.mp3 c.wav -o final.mp3
  py audio_tool.py pipeline input.mp3 -o vocals_clean.mp3

需求：
  - Python 3.9+
  - 系統需安裝 ffmpeg 並可在 PATH 中執行
  - py -m pip install -r requirements.txt
"""

import argparse
import os
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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = out_path.suffix.lstrip(".").lower() or "mp3"
    export_kwargs = {}
    if fmt == "mp3":
        export_kwargs["bitrate"] = "320k"
    result.export(out_path, format=fmt, **export_kwargs)

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
            combined = combined.append(seg, crossfade=crossfade)

    out_path = Path(output_path)
    if out_path.suffix.lower() != ".mp3":
        out_path = out_path.with_suffix(".mp3")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _info(f"輸出 MP3：{out_path} (bitrate={bitrate}) …")
    combined.export(out_path, format="mp3", bitrate=bitrate)

    _info(f"完成！總長 {len(combined)/1000:.1f}s → {out_path}")
    return str(out_path)


# --------------------------------------------------------------------------- #
# 功能 4：影片轉音檔
# --------------------------------------------------------------------------- #
def extract_audio(
    input_path: str,
    output_path: str,
    bitrate: str = "320k",
) -> str:
    """
    從影片（mp4 / mkv / mov…）抽出聲音，輸出成音檔。

    輸出格式由 output_path 副檔名決定（.mp3 或 .wav，預設 mp3）。
    """
    _check_ffmpeg()
    in_path = Path(input_path)
    if not in_path.is_file():
        sys.exit(f"錯誤：找不到輸入檔 {in_path}")

    out_path = Path(output_path)
    if out_path.suffix.lower() not in (".mp3", ".wav"):
        out_path = out_path.with_suffix(".mp3")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = out_path.suffix.lower()
    cmd = ["ffmpeg", "-y", "-i", str(in_path), "-vn"]
    if fmt == ".wav":
        cmd += ["-acodec", "pcm_s16le"]
    else:  # mp3
        cmd += ["-acodec", "libmp3lame", "-b:a", bitrate]
    cmd += [str(out_path), "-loglevel", "error", "-stats"]

    _info(f"從 {in_path.name} 抽取音訊 → {out_path} …")
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)
    if result.returncode != 0:
        sys.exit("錯誤：ffmpeg 抽取音訊失敗。")

    _info(f"完成！輸出：{out_path}")
    return str(out_path)


# --------------------------------------------------------------------------- #
# 功能 5：批次處理整個資料夾
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
    elif args.command == "batch":
        batch_process(args.input_dir, args.output, op=args.op,
                      device=args.device, recursive=args.recursive,
                      silence_thresh=args.silence_thresh,
                      min_silence_len=args.min_silence_len,
                      model=args.model)


if __name__ == "__main__":
    main()
