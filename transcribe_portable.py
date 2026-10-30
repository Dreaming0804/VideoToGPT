# -*- coding: utf-8 -*-
"""Offline multilingual transcription. Imported by the frozen main EXE worker mode."""
from __future__ import annotations
from pathlib import Path
import os


def timestamp(seconds: float, *, srt: bool = False) -> str:
    ms = max(0, round(float(seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d}{',' if srt else '.'}{ms:03d}"


def transcribe(audio: Path, outdir: Path, model_dir: Path, language: str | None) -> int:
    if not audio.is_file():
        raise FileNotFoundError(str(audio))
    if not (model_dir / "model.bin").is_file():
        raise FileNotFoundError("离线模型不完整：" + str(model_dir))
    outdir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_OFFLINE"] = "1"
    from faster_whisper import WhisperModel
    print("加载随软件附带的 Whisper-small 模型...", flush=True)
    model = WhisperModel(str(model_dir), device="cpu", compute_type="int8", local_files_only=True)
    segments, info = model.transcribe(
        str(audio), language=language, multilingual=True, vad_filter=True,
        condition_on_previous_text=False, beam_size=5,
    )
    txt = outdir / "transcript.txt"
    srt = outdir / "subtitles.srt"
    count = 0
    with txt.open("w", encoding="utf-8-sig") as t, srt.open("w", encoding="utf-8-sig") as s:
        t.write("机器识别的字幕（未经校对，可能有漏词或错词）\n")
        t.write(f"初始语言检测：{info.language}；设置语言：{language or '自动'}\n\n")
        for segment in segments:
            content = segment.text.strip()
            if not content:
                continue
            count += 1
            t.write(f"[{timestamp(segment.start)} - {timestamp(segment.end)}] {content}\n")
            s.write(f"{count}\n{timestamp(segment.start, srt=True)} --> "
                    f"{timestamp(segment.end, srt=True)}\n{content}\n\n")
            print(f"[{timestamp(segment.start)} - {timestamp(segment.end)}] {content}", flush=True)
    print(f"转录结束，共 {count} 个片段。", flush=True)
    return 0
