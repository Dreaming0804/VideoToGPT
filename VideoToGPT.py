# -*- coding: utf-8 -*-
"""VideoToGPT portable Windows desktop application.
Runtime depends only on binaries/model shipped alongside the built executable.
Copyright (c) 2026. Source is provided for personal modification.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import queue
import shutil
import subprocess
import sys
import time
import threading
import traceback
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from content_extract import ensure_public_url, likely_video_site
from content_extract import probe, extract_url, ExtractionError, save_images, is_xhs
from xhs_comments import collect as collect_xhs_comments
from video_resolver import discover_video, VideoSource



def app_dir() -> Path:
    # Unlike __file__, sys.executable is outside _internal in PyInstaller onedir.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = app_dir()
SETTINGS_FILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VideoToGPT" / "settings.json"
# In development, keep outputs in the test folder but reuse the validated
# standalone program's binaries/model, avoiding a second 500 MB copy.
if getattr(sys, "frozen", False):
    ASSETS = ROOT
else:
    candidates = [
        Path(os.environ.get("VIDEOTOGPT_ASSET_DIR", "")) if os.environ.get("VIDEOTOGPT_ASSET_DIR") else None,
        ROOT.parent / "VideoToGPT_独立版构建" / "dist" / "VideoToGPT",
        Path("D:/Apps/VideoToGPT"),
        ROOT,
    ]
    ASSETS = next((p for p in candidates if p and (p / "tools" / "ffmpeg.exe").is_file()), ROOT)
TOOLS = ASSETS / "tools"
MODEL = ASSETS / "models" / "small"
def output_dir() -> Path:
    override = os.environ.get("VIDEOTOGPT_OUTPUT_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        saved = settings.get("output_directory", "")
        if saved:
            return Path(saved).expanduser().resolve()
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass
    if getattr(sys, "frozen", False):
        return ROOT / "output"
    # Dev builds and installed v0.1 can share one destination by default.
    for installed in (Path("D:/Apps/VideoToGPT_0.3"), Path("D:/Apps/VideoToGPT")):
        if installed.is_dir():
            return installed / "output"
    return ROOT / "output"


def set_output_dir(path: Path) -> None:
    global OUTPUT
    destination = Path(path).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps({"output_directory": str(destination)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    OUTPUT = destination

OUTPUT = output_dir()
HIDE_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
APP_TITLE = "VideoToGPT 0.3.2 · 通用视频优先 / 可选评论"
LANGUAGES = {
    "自动识别（多语言）": "auto", "英语为主": "en", "中文为主": "zh",
    "日语为主": "ja", "韩语为主": "ko",
}
DENSITIES = {"标准 · 每秒1张": 1.0, "快速 · 每5秒1张": 0.2, "精细 · 每秒2张": 2.0}
VIDEO_PROMPT = (
    "请分析我上传的 VideoToGPT 视频资料包。\n"
    "请根据 source_url.txt、video_source_info.json、page_content.txt（若存在）、transcript.txt、sheets/ 和 frames/ 实际内容，按时间顺序分析视频。\n"
    "请优先依据画面、内嵌字幕及能够相互印证的语音内容；机器转录存在错词、漏词。\n"
    "说明主题、关键人物/操作、重要对白、事件顺序与时间点；专业术语不确定时标注待核实。\n"
    "如果某个动作/画面发生在抽帧间隔内，不要声称已经连续观看，指出需要补帧的时间段。\n"
    "不要根据标题或猜测补写无法确认的信息。"
)


CONTENT_PROMPT = (
    "请阅读我上传的 VideoToGPT 网页资料包。\n"
    "先根据 source_url.txt、page_content.txt 与 images/ 中实际保存的图片，解释这篇笔记或网页讲了什么。\n"
    "尊重图片的原始顺序，结合正文分析；不要把封面预览图当作整个图集。\n"
    "对没有获取到的内容（如无法访问的图集或登录限制）明确说明，不能凭标题补写。"
)
COMBINED_PROMPT = (
    "请先读取同一条小红书笔记的 source_url.txt、page_content.txt 或视频的 "
    "transcript.txt、images/、sheets/、frames/，说明原帖实际讲了什么。\n"
    "再读取 comments.txt 和 comment_stats.txt，分析评论观点、玩梗、争论及与原帖的关系。\n"
    "comments.json 保留评论ID与回复关系；comment_coverage.json 说明采集完整性。\n"
    "必须区分原作者的表达与网友评论，不要把评论反推成原帖的事实，" 
    "不能凭重复评论直接判定账号是机器人。"
)
PROMPT = VIDEO_PROMPT


def bin_path(name: str) -> Path:
    target = TOOLS / (name + ".exe")
    if not target.is_file():
        raise FileNotFoundError(f"缺少独立版必需文件：{target}\n请重新运行『一键制作便携版.cmd』。")
    return target


def run_cmd(args: list[str], cwd: Path, env: dict[str, str], log) -> None:
    cmd = [str(a) for a in args]
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
        text=True, bufsize=1, creationflags=HIDE_WINDOW,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip("\r\n"))
    status = proc.wait()
    if status:
        raise RuntimeError(f"外部程序退出码 {status}。查看窗口日志定位原因。")



def run_transcription_worker(args: list[str], cwd: Path, env: dict[str, str], log, logfile: Path) -> None:
    """Launch GUI-compiled EXE as a worker; tail the text log while it runs."""
    proc = subprocess.Popen(
        [str(a) for a in args], cwd=str(cwd), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=HIDE_WINDOW,
    )
    position = 0

    def read_progress():
        nonlocal position
        if not logfile.exists():
            return
        with logfile.open("r", encoding="utf-8", errors="replace") as stream:
            stream.seek(position)
            new = stream.read()
            position = stream.tell()
        for line in new.splitlines():
            if line.strip():
                log(line)

    while proc.poll() is None:
        read_progress()
        time.sleep(0.5)
    read_progress()
    if proc.returncode != 0:
        raise RuntimeError(f"音频识别子程序退出码 {proc.returncode}；请查看识别日志。")


def create_package(job: Path, log) -> Path:
    archive_path = job / "UploadToChatGPT.zip"
    manifest = ["source_url.txt", "README_分析说明.txt", "analysis_prompt.txt", "transcript.txt", "subtitles.srt", "page_content.txt", "download_warnings.txt", "comments.json", "comments.txt", "comment_stats.txt", "comment_coverage.json", "README_评论说明.txt", "comments_warning.txt", "video_source_info.json"]
    files = [job / name for name in manifest if (job / name).is_file()]
    for dirname in ("sheets", "frames", "images"):
        if (job / dirname).is_dir():
            files.extend(sorted(p for p in (job / dirname).iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")))
    log(f"正在压缩 {len(files)} 个资料文件...")
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=4, allowZip64=True) as z:
        for path in files:
            z.write(path, path.relative_to(job))
    return archive_path


def transcribe_cli(args: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="VideoToGPT --transcribe")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--language", default="auto")
    parser.add_argument("--logfile", default=None)
    opts = parser.parse_args(args)
    if opts.logfile:
        # Windowed PyInstaller applications may have no sys.stdout at all.
        # Explicitly record worker progress for the GUI to read.
        sys.stdout = open(opts.logfile, "w", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
    from transcribe_portable import transcribe
    return transcribe(
        Path(opts.audio), Path(opts.outdir), MODEL,
        None if opts.language == "auto" else opts.language,
    )


def inspect_install() -> list[str]:
    report = [f"程序位置：{ROOT}", f"输出位置：{OUTPUT}"]
    for tool in ("yt-dlp", "ffmpeg", "ffprobe", "deno"):
        exe = bin_path(tool)
        report.append(f"[OK] {tool}: {exe.stat().st_size:,} 字节")
    for file in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        p = MODEL / file
        if not p.is_file() or not p.stat().st_size:
            raise RuntimeError(f"模型文件缺失：{p}")
        report.append(f"[OK] {p.name}: {p.stat().st_size:,} 字节")
    import av
    import ctranslate2
    from playwright.sync_api import sync_playwright
    from faster_whisper import WhisperModel
    report.append(f"[OK] PyAV: {av.__version__}; CTranslate2: {ctranslate2.__version__}")
    # Actually load the *bundled* model, to detect missing DLLs/data files.
    m = WhisperModel(str(MODEL), device="cpu", compute_type="int8", local_files_only=True)
    report.append("[OK] 本地 small 模型 CPU INT8 加载成功")
    del m
    return report


def self_check(destination: str) -> int:
    try:
        lines = inspect_install()
        lines.append("PASS: 独立运行环境检查通过")
        status = 0
    except Exception:
        lines = [traceback.format_exc(), "FAIL: 请检查打包日志或目录结构"]
        status = 2
    Path(destination).write_text("\n".join(lines), encoding="utf-8-sig")
    return status


def process_video(url: str, language: str, fps: float, emit,
                  *, original_url: str | None = None, source: VideoSource | None = None,
                  original_page=None) -> tuple[Path, Path]:
    yt, ffmpeg, ffprobe, deno = (bin_path(x) for x in ("yt-dlp", "ffmpeg", "ffprobe", "deno"))
    if not (MODEL / "model.bin").is_file():
        raise FileNotFoundError("找不到 models\\small\\model.bin，独立版模型没有复制完整。")
    token = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    job = OUTPUT / ("video_" + token)
    frames = job / "frames"
    sheets = job / "sheets"
    frames.mkdir(parents=True, exist_ok=False)
    sheets.mkdir(parents=True, exist_ok=True)
    requested_url = original_url or url
    (job / "source_url.txt").write_text(requested_url + "\n", encoding="utf-8")
    (job / "analysis_prompt.txt").write_text(PROMPT, encoding="utf-8-sig")
    info = {
        "source_url": requested_url,
        "method": source.method if source else "现有平台视频提取",
        "title": (original_page.title if original_page and original_page.title else (source.title if source else "")),
        "description": (original_page.body if original_page and original_page.body else (source.description if source else "")),
        "author": original_page.author if original_page else "",
        "extractor": source.extractor if source else "",
    }
    (job / "video_source_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    if info["title"] or info["description"]:
        (job / "page_content.txt").write_text(
            "标题：" + info["title"] + "\n作者：" + info["author"] +
            "\n原始链接：" + requested_url + "\n\n网页介绍：\n" + info["description"],
            encoding="utf-8-sig")
    env = os.environ.copy()
    env.update({
        "PATH": str(TOOLS) + os.pathsep + env.get("PATH", ""),
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })

    def log(msg: str):
        if msg.strip():
            emit(("log", msg))

    log("保存到：" + str(job))
    yt_common = [str(yt), "--ignore-config", "--no-playlist", "--playlist-items", "1", "--ffmpeg-location", str(TOOLS), "--no-progress"]
    if source and source.referer and source.media_url != source.source_url:
        yt_common += ["--referer", source.referer]
    log("【1/5】尝试提取音频")
    audio = job / "audio.mp3"
    audio_ok = False
    try:
        run_cmd(yt_common + ["-f", "bestaudio/best", "-x", "--audio-format", "mp3",
                             "-o", str(job / "audio.%(ext)s"), "--", url], job, env, log)
        audio_ok = audio.is_file()
    except Exception as exc:
        log("本视频可能没有音轨，或音轨无法下载；将继续处理画面：" + str(exc))
    if not audio_ok:
        log("没有取得可识别音频，本次只保留视频画面。")

    log("【2/5】下载视频画面（优先不超过720p）")
    try:
        run_cmd(yt_common + ["-f", "bv[height<=720]/b[height<=720]/bv/best",
                             "-o", str(job / "video.%(ext)s"), "--", url], job, env, log)
    except Exception as exc:
        log("标准画质选择失败，尝试通用 best 格式：" + str(exc))
        run_cmd(yt_common + ["-f", "best", "-o", str(job / "video.%(ext)s"),
                             "--", url], job, env, log)
    videos = sorted(p for p in job.glob("video.*") if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".flv"))
    if not videos:
        raise RuntimeError("视频下载完毕但未找到视频文件")
    video = videos[0]
    log("【3/5】抽取视频帧")
    run_cmd([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
             "-vf", f"fps={fps:g},scale=1280:-2", "-q:v", "3", "-start_number", "0",
             str(frames / "frame_%06d.jpg")], job, env, log)
    nframes = len(list(frames.glob("*.jpg")))
    if nframes < 1:
        raise RuntimeError("未生成任何视频画面")
    log(f"视频截图：{nframes}张")
    log("【4/5】制作6宫格总览图")
    try:
        run_cmd([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                 "-vf", "fps=1/5,scale=480:-2,tile=3x2", "-fps_mode", "vfr", "-q:v", "3",
                 str(sheets / "sheet_%03d.jpg")], job, env, log)
    except Exception as exc:
        log(f"拼图未生成，保留原始截图：{exc}")

    log("【5/5】本地多语言语音识别（离线模型）")
    try:
        if not audio_ok:
            raise FileNotFoundError("没有音轨，跳过转录")
        # The same PyInstaller EXE acts as GUI and as the transcription worker.
        if getattr(sys, "frozen", False):
            child = [str(Path(sys.executable)), "--transcribe"]
        else:
            child = [sys.executable, str(Path(__file__).resolve()), "--transcribe"]
        log_path = job / "transcription.log"
        run_transcription_worker(
            child + ["--audio", str(audio), "--outdir", str(job),
                     "--language", language, "--logfile", str(log_path)],
            job, env, log, log_path,
        )
        transcript_status = "成功（机器转录，未人工校对）"
    except Exception as exc:
        transcript_status = "跳过（无音频）" if not audio_ok else "失败（仍可用截图分析）"
        log(("语音转录已跳过：" if not audio_ok else "转录失败：") + str(exc))

    note = (
        "VideoToGPT 视频分析资料\n"
        f"视频链接：{requested_url}\n"
        f"逐帧截图：{nframes} 张；间隔约 {1 / fps:g} 秒\n"
        f"语音转录：{transcript_status}；指定语言：{language}\n"
        "frames/：按序号排列，frame_000000 对应视频开头，随后以设定间隔递增（近似值）。\n"
        "sheets/：每5秒一图的6宫格总览；每张展示约30秒。\n"
        "音频和完整视频只保留本机，不包括在 UploadToChatGPT.zip 中。\n"
        "video_source_info.json：网页视频来源信息及可获取的标题、简介。\n"
        "时间戳、OCR及专业词汇可能有误；抽帧不能代替完整播放。\n"
        "若视频含有很多短暂文字/快速动作，请补充相关时段密集帧。\n"
    )
    (job / "README_分析说明.txt").write_text(note, encoding="utf-8-sig")
    # Delay packaging when a same-note comment collection is requested.
    result = job / "UploadToChatGPT.zip"
    return job, result


def process_page(page, emit) -> tuple[Path, Path]:
    """Handle image notes/articles without invoking yt-dlp, FFmpeg or Whisper."""
    token = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    job = OUTPUT / ("page_" + token)
    job.mkdir(parents=True, exist_ok=False)
    def log(msg):
        emit(("log", msg))
    log("内容类型：" + ("图文笔记" if page.kind == "image_note" else "普通网页文章"))
    log("结果目录：" + str(job))
    (job / "source_url.txt").write_text(page.url + "\n", encoding="utf-8")
    (job / "analysis_prompt.txt").write_text(CONTENT_PROMPT, encoding="utf-8-sig")
    lines = [f"标题：{page.title or '未获得'}", f"作者：{page.author or '未获得'}",
             f"来源：{page.url}", f"类型：{page.kind}", "", "正文：", page.body]
    (job / "page_content.txt").write_text("\n".join(lines), encoding="utf-8-sig")
    if not page.body.strip() and not page.image_urls:
        raise ExtractionError("网页未提取到正文和图片，已停止生成空资料包。")
    images, errors = save_images(page, job / "images", log)
    all_warnings = page.warnings + errors
    if all_warnings:
        (job / "download_warnings.txt").write_text("\n".join(all_warnings), encoding="utf-8-sig")
    if page.kind == "image_note" and page.image_urls and not images:
        raise ExtractionError("已读取笔记正文，但图片下载全部失败；未生成假装完整的资料包。笔记文字已保存在 " + str(job))
    note = ["VideoToGPT 0.2 网页内容资料", "内容类型：" + page.kind,
            f"正文长度：{len(page.body)} 字符", f"图片候选：{len(page.image_urls)}，成功：{len(images)}",
            "下载状态：" + ("部分成功，见 download_warnings.txt" if all_warnings else "成功"),
            "page_content.txt：正文和来源", "images/：按顺序保存的实际图片",
            "网站登录/风控造成的缺失不会用猜测填充。"]
    (job / "README_分析说明.txt").write_text("\n".join(note), encoding="utf-8-sig")
    result = job / "UploadToChatGPT.zip"
    log("原帖内容已提取，等待最终统一打包。")
    if all_warnings:
        log("注意：部分内容缺失，详细情况已写入 download_warnings.txt")
    return job, result


def process(link: str, language: str, fps: float, emit,
            include_comments: bool = False,
            comment_stop: threading.Event | None = None,
            comment_scroll: threading.Event | None = None) -> tuple[Path, Path]:
    def log(msg: str):
        emit(("log", msg))
    url = extract_url(link)
    log("【1】识别链接类型：" + url)
    # Video-first on unknown websites. This is the core change: an article
    # embedding a playable media stream no longer becomes a text-only package.
    # Exception: Xiaohongshu notes MUST first use their existing type-specific
    # parser, so image notes are not mistaken for videos because of their cover.
    source = None
    page = None
    if is_xhs(url):
        log("小红书链接：先区分视频笔记与图文笔记。")
        try:
            page = probe(url)
        except ExtractionError as exc:
            log("笔记正文暂时不可读：" + str(exc))
            source, notes = discover_video(url, TOOLS / "yt-dlp.exe", log, fetch_html=False)
            if source is None:
                raise ExtractionError("小红书笔记解析失败，也未找到可提取的视频。") from exc
    else:
        # Existing, well-supported video sites go through their yt-dlp
        # extractors directly. Unknown sites get generic metadata-first probe.
        if likely_video_site(url):
            from content_extract import Page
            page = Page(url, 'video')
        else:
            log("【通用视频探测】先检查网页及其嵌入视频，不下载媒体。")
            source, notes = discover_video(url, TOOLS / "yt-dlp.exe", log)
            if source:
                log("找到视频：" + source.method)
            else:
                log("暂未确认可用视频流，继续尝试现有图文提取方式。")
                try:
                    page = probe(url)
                except ExtractionError as exc:
                    detail = "；".join(x for x in notes if x)[:350]
                    raise ExtractionError(str(exc) + "\n视频探测：" + detail) from exc
    if source or (page and page.kind == "video"):
        log("检测到视频，交给原有音视频处理流程。")
        job, archive = process_video(
            source.media_url if source else page.url, language, fps, emit,
            original_url=url, source=source, original_page=page,
        )
    elif page:
        job, archive = process_page(page, emit)
    else:
        raise ExtractionError("未找到可下载的视频或可提取的网页内容。")
    if include_comments and is_xhs(url):
        # Capture comments for THIS SAME URL after extracting its original content.
        # Comment data are placed in the same job, not a separate output folder.
        log("【小红书评论】原帖已提取；准备打开 Edge 读取实际加载的评论。")
        (job / "analysis_prompt.txt").write_text(COMBINED_PROMPT, encoding="utf-8-sig")
        emit(("comments_start", True))
        comment_result = {"count": 0, "error": None, "coverage": None}
        def forward(message):
            kind, data = message
            if kind == "count":
                comment_result["count"] = data
                emit(("comment_count", data))
            elif kind == "done":
                comment_result["count"] = data[2]
            elif kind == "coverage":
                comment_result["coverage"] = data
            elif kind == "error":
                comment_result["error"] = data
                log("评论采集失败：" + str(data))
            elif kind == "log":
                log("[评论] " + str(data))
        try:
            collect_xhs_comments(url, comment_stop or threading.Event(),
                                 comment_scroll or threading.Event(), forward,
                                 target_dir=job)
        finally:
            emit(("comments_end", True))
        if comment_result["error"]:
            (job / "comments_warning.txt").write_text(
                "评论采集未成功：" + str(comment_result["error"]), encoding="utf-8-sig"
            )
        elif comment_result["count"] == 0:
            log("未获取到评论，不表示帖子没有评论；请查看浏览器/说明文件。")
        if comment_result["coverage"]:
            state = comment_result["coverage"]["status"]
            log("评论采集状态：" + state)
            if not (comment_result["coverage"].get("root_end_observed") and
                    comment_result["coverage"].get("auto_end") and
                    not comment_result["coverage"].get("reported_reply_gaps")):
                (job / "comments_warning.txt").write_text(
                    "注意：评论未确认完整。请查看 comment_coverage.json 与 comment_stats.txt。\n",
                    encoding="utf-8-sig")
        log(f"原帖与 {comment_result['count']} 条实际采集的评论已整合。")
    # Package exactly once, even for Bilibili-only or article-only tasks.
    archive = create_package(job, log)
    log("已生成同一任务的资料包：" + str(archive))
    return job, archive



def main_gui() -> int:
    import tkinter as tk
    from tkinter import ttk, messagebox

    class Window:
        def __init__(self, root):
            self.root = root
            root.title(APP_TITLE)
            root.geometry("860x710")
            root.minsize(750, 550)
            self.events = queue.Queue()
            self.running = False
            self.last_job: Path | None = None
            self.comments_partial: bool = False
            self.url = tk.StringVar()
            self.lang = tk.StringVar(value="自动识别（多语言）")
            self.density = tk.StringVar(value="标准 · 每秒1张")
            self.with_comments = tk.BooleanVar(value=False)
            self.comment_stop = threading.Event()
            self.comment_scroll = threading.Event()
            self.comment_running = False
            self.status = tk.StringVar(value="准备就绪。粘贴链接或分享文案并点击开始。")
            self.build()
            root.after(100, self.poll)

        def build(self):
            outer = ttk.Frame(self.root, padding=18)
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text="VideoToGPT · 独立便携版", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
            ttk.Label(outer, text="通用视频链接 → 音频与关键帧 / 小红书图文 → 正文原图 / 评论可选",
                      foreground="#666666").pack(anchor="w", pady=(2, 13))
            ttk.Label(outer, text="网页或视频链接（可直接粘贴小红书完整分享文案）：").pack(anchor="w")
            link_row = ttk.Frame(outer)
            link_row.pack(fill="x", pady=(5, 12))
            ttk.Entry(link_row, textvariable=self.url).pack(side="left", fill="x", expand=True)
            ttk.Button(link_row, text="粘贴", command=self.paste).pack(side="left", padx=(8, 0))
            opts = ttk.Frame(outer)
            opts.pack(anchor="w", fill="x", pady=(0, 12))
            ttk.Label(opts, text="语言：").pack(side="left")
            ttk.Combobox(opts, textvariable=self.lang, values=list(LANGUAGES),
                         state="readonly", width=20).pack(side="left", padx=(3, 17))
            ttk.Label(opts, text="抽帧：").pack(side="left")
            ttk.Combobox(opts, textvariable=self.density, values=list(DENSITIES),
                         state="readonly", width=19).pack(side="left", padx=(3, 0))
            ttk.Checkbutton(
                outer,
                text="额外采集小红书评论（可选，默认关闭；可能打开专用 Edge）",
                variable=self.with_comments,
            ).pack(anchor="w", pady=(0, 9))
            buttons = ttk.Frame(outer)
            buttons.pack(fill="x", pady=(0, 10))
            self.start_button = ttk.Button(buttons, text="▶ 开始处理", command=self.start)
            self.start_button.pack(side="left")
            self.scroll_button = ttk.Button(buttons, text="暂停自动采集", command=self.toggle_comment_scroll,
                                            state="disabled")
            self.scroll_button.pack(side="left", padx=(7, 0))
            self.finish_button = ttk.Button(buttons, text="提前结束评论", command=self.finish_comments,
                                            state="disabled")
            self.finish_button.pack(side="left", padx=(7, 0))
            ttk.Button(buttons, text="打开输出目录", command=self.open_output).pack(side="left", padx=(7, 0))
            ttk.Button(buttons, text="设置输出位置", command=self.choose_output).pack(side="left", padx=(7, 0))
            ttk.Button(buttons, text="复制GPT指令", command=self.copy_prompt).pack(side="left", padx=(7, 0))
            ttk.Button(buttons, text="检查环境", command=self.check).pack(side="left", padx=(7, 0))
            ttk.Button(buttons, text="桌面快捷方式", command=self.shortcut).pack(side="left", padx=(7, 0))
            self.output_info = tk.StringVar(value="资料包统一保存至：" + str(OUTPUT))
            ttk.Label(outer, textvariable=self.output_info, foreground="#555555", wraplength=800).pack(anchor="w", pady=(2, 4))
            ttk.Label(outer, textvariable=self.status, foreground="#236298", wraplength=795).pack(anchor="w", pady=(5, 6))
            logs = ttk.Frame(outer)
            logs.pack(fill="both", expand=True)
            self.log_view = tk.Text(logs, font=("Consolas", 10), state="disabled", wrap="word")
            bar = ttk.Scrollbar(logs, orient="vertical", command=self.log_view.yview)
            self.log_view.configure(yscrollcommand=bar.set)
            self.log_view.pack(side="left", expand=True, fill="both")
            bar.pack(side="right", fill="y")
            ttk.Label(outer, text="同一链接 → 视频/图文 → 单个 ZIP；评论按需勾选。未验证的网站自动尝试通用视频解析。",
                      foreground="#666666").pack(anchor="w", pady=(8, 0))

        def paste(self):
            try:
                self.url.set(self.root.clipboard_get().strip())
            except Exception:
                messagebox.showinfo("提示", "剪贴板里没有文字。")

        def add_log(self, value):
            self.log_view.configure(state="normal")
            self.log_view.insert("end", value + "\n")
            self.log_view.see("end")
            self.log_view.configure(state="disabled")

        def start(self):
            url = self.url.get().strip()
            try:
                extract_url(url)
            except ExtractionError as exc:
                messagebox.showwarning("无效链接", str(exc))
                return
            if self.running:
                return
            self.running = True
            self.comment_running = False
            self.comment_stop.clear()
            self.comment_scroll.set()
            self.scroll_button.configure(state="disabled", text="暂停自动采集")
            self.finish_button.configure(state="disabled")
            self.last_job = None
            self.comments_partial = False
            self.start_button.configure(state="disabled")
            self.log_view.configure(state="normal")
            self.log_view.delete("1.0", "end")
            self.log_view.configure(state="disabled")
            self.status.set("正在处理；可在日志中查看进度。")
            lang = LANGUAGES[self.lang.get()]
            fps = DENSITIES[self.density.get()]
            capture_comments = bool(self.with_comments.get())

            def task():
                try:
                    job, archive = process(url, lang, fps, self.events.put,
                                           include_comments=capture_comments,
                                           comment_stop=self.comment_stop,
                                           comment_scroll=self.comment_scroll)
                    self.events.put(("done", (str(job), str(archive))))
                except Exception:
                    self.events.put(("error", traceback.format_exc()))
            threading.Thread(target=task, daemon=True).start()

        def toggle_comment_scroll(self):
            if not self.comment_running:
                return
            if self.comment_scroll.is_set():
                self.comment_scroll.clear()
                self.scroll_button.configure(text="继续自动采集")
                self.add_log("自动滚动已暂停。")
            else:
                self.comment_scroll.set()
                self.scroll_button.configure(text="暂停自动采集")
                self.add_log("自动滚动已继续；若平台要求登录，将停止本次评论采集。")

        def finish_comments(self):
            if not self.comment_running:
                return
            self.comment_scroll.clear()
            self.comment_stop.set()
            self.finish_button.configure(state="disabled")
            self.status.set("正在提前停止采集，保存实际收到的评论，并生成 ZIP……")

        def poll(self):
            try:
                while True:
                    kind, value = self.events.get_nowait()
                    if kind == "log":
                        self.add_log(value)
                    elif kind == "comments_start":
                        self.comment_running = True
                        self.scroll_button.configure(state="normal")
                        self.finish_button.configure(state="normal")
                        self.status.set("评论采集中；若网站弹出登录验证，程序会结束采集并标记不完整。")
                    elif kind == "comment_count":
                        self.status.set(f"已实际采集 {value} 条评论及回复，继续自动读取分页……")
                    elif kind == "comments_end":
                        self.comment_running = False
                        self.scroll_button.configure(state="disabled")
                        self.finish_button.configure(state="disabled")
                    elif kind == "done":
                        self.running = False
                        self.comment_running = False
                        self.scroll_button.configure(state="disabled")
                        self.finish_button.configure(state="disabled")
                        self.start_button.configure(state="normal")
                        self.last_job = Path(value[0])
                        self.status.set("资料包已生成。评论为可选功能；有评论时请核对采集范围报告。")
                        messagebox.showinfo("完成", f"结果保存在：\n{value[1]}")
                    elif kind == "error":
                        self.running = False
                        self.comment_running = False
                        self.scroll_button.configure(state="disabled")
                        self.finish_button.configure(state="disabled")
                        self.start_button.configure(state="normal")
                        self.status.set("处理失败，请查看日志。")
                        self.add_log(value)
                        messagebox.showerror("处理失败", value[-950:])
            except queue.Empty:
                pass
            self.root.after(100, self.poll)

        def choose_output(self):
            from tkinter import filedialog
            if self.running:
                messagebox.showwarning("请稍后", "当前任务结束后再更改输出位置。")
                return
            selected = filedialog.askdirectory(title="选择所有资料包的统一保存目录", initialdir=str(OUTPUT))
            if not selected:
                return
            try:
                set_output_dir(Path(selected))
                self.output_info.set("资料包统一保存至：" + str(OUTPUT))
                self.status.set("输出位置已保存，下次启动仍将使用此目录。")
            except OSError as exc:
                messagebox.showerror("设置失败", str(exc))

        def open_output(self):
            path = self.last_job or OUTPUT
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))

        def copy_prompt(self):
            self.root.clipboard_clear()
            self.root.clipboard_append(
                "请读取我上传的 VideoToGPT 资料包，依据其中真实的图片、正文、字幕和来源信息分析。"
                "先检查 README 和 warnings；不要把没有提取成功的内容当成已读取。"
                "请按原有顺序说明主要内容，并回答我接下来提出的问题。"
            )
            self.status.set("GPT分析指令已复制到剪贴板。")

        def check(self):
            def task():
                try:
                    lines = inspect_install()
                    self.events.put(("log", "\n".join(lines)))
                    self.events.put(("log", "✅ 环境检查通过"))
                except Exception:
                    self.events.put(("log", "检查失败：\n" + traceback.format_exc()))
            threading.Thread(target=task, daemon=True).start()

        def shortcut(self):
            if not getattr(sys, "frozen", False):
                messagebox.showinfo("提示", "请在独立版 EXE 中创建桌面快捷方式。")
                return
            quoted = lambda s: "'" + str(s).replace("'", "''") + "'"
            script = (
                "$d=[Environment]::GetFolderPath('Desktop');"
                "$shell=New-Object -ComObject WScript.Shell;"
                "$s=$shell.CreateShortcut((Join-Path $d 'VideoToGPT.lnk'));"
                f"$s.TargetPath={quoted(sys.executable)};"
                f"$s.WorkingDirectory={quoted(ROOT)};"
                f"$s.IconLocation={quoted(str(sys.executable) + ',0')};"
                "$s.Save()"
            )
            try:
                subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                               check=True, capture_output=True, text=True, creationflags=HIDE_WINDOW)
                messagebox.showinfo("成功", "桌面快捷方式已创建。以后直接双击打开。")
            except Exception as exc:
                messagebox.showerror("创建失败", str(exc))

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    Window(root)
    root.mainloop()
    return 0


def main() -> int:
    # PyInstaller --windowed can set stdout/stderr to None. Some native
    # libraries expect writable streams even in self-check / GUI mode.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "--transcribe":
        return transcribe_cli(sys.argv[2:])
    if len(sys.argv) == 3 and sys.argv[1] == "--self-check":
        return self_check(sys.argv[2])
    return main_gui()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # Keep a diagnostic even when running with --windowed (no console).
        try:
            (ROOT / "启动错误.log").write_text(traceback.format_exc(), encoding="utf-8-sig")
        except Exception:
            pass
        raise
