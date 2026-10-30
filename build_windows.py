# -*- coding: utf-8 -*-
"""One-click Windows onedir release build for VideoToGPT 0.3.2.

Builds in a configured Windows Python environment and copies existing
media tools and a previously downloaded Whisper model. Does not modify v0.1.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_default_target = Path('D:/Apps/VideoToGPT_0.3.2') if Path('D:/').is_dir() else Path.home() / 'VideoToGPT_0.3.2'
TARGET = Path(os.getenv('VIDEOTOGPT_INSTALL_DEST', str(_default_target)))
STAGE = HERE / '.release_work'
WORK = STAGE / 'dist' / 'VideoToGPT'
LOG = HERE / 'build_log.txt'
REQUIRED_MODEL = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')


def report(line: str):
    print(line, flush=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def run(args, cwd=HERE):
    display = ' '.join(map(str, args))
    report('RUN: ' + display)
    proc = subprocess.run([str(x) for x in args], cwd=str(cwd), text=True,
                          encoding='utf-8', errors='replace', stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    if proc.stdout:
        with LOG.open('a', encoding='utf-8') as f:
            f.write(proc.stdout)
        print(proc.stdout[-5000:], flush=True)
    if proc.returncode:
        raise RuntimeError(f'退出码 {proc.returncode}：{display}；完整错误见 build_log.txt')


def find_tools():
    custom_tools = os.getenv('VIDEOTOGPT_TOOLS_DIR', '').strip()
    choices = [
        Path(custom_tools) if custom_tools else HERE / 'tools',
        Path('D:/Apps/VideoToGPT/tools'),
        Path('D:/VideoText/VideoToGPT_独立版构建/dist/VideoToGPT/tools'),
        HERE / 'tools',
    ]
    mapping = {}
    for name in ('yt-dlp', 'ffmpeg', 'ffprobe', 'deno'):
        path = next((d / f'{name}.exe' for d in choices if (d / f'{name}.exe').is_file()), None)
        if not path:
            found = shutil.which(name)
            if found:
                path = Path(found)
        if not path or not path.is_file():
            raise RuntimeError(f'未找到 {name}.exe。请保留以前已成功安装的 yt-dlp / FFmpeg / Deno。')
        mapping[name] = path
    return mapping


def find_model():
    custom_model = os.getenv('VIDEOTOGPT_MODEL_DIR', '').strip()
    choices = [
        Path(custom_model) if custom_model else HERE / 'models' / 'small',
        HERE / 'models' / 'small',
        Path('D:/Apps/VideoToGPT/models/small'),
        Path('D:/VideoText/VideoToGPT_独立版构建/dist/VideoToGPT/models/small'),
    ]
    hub_roots = [
        Path('D:/VideoText/models/hub/models--Systran--faster-whisper-small/snapshots'),
        Path.home() / '.cache' / 'huggingface' / 'hub' / 'models--Systran--faster-whisper-small' / 'snapshots',
    ]
    for hub_root in hub_roots:
        if hub_root.is_dir():
            choices.extend([p for p in hub_root.iterdir() if p.is_dir()])
    for folder in choices:
        if all((folder / f).is_file() and (folder / f).stat().st_size > 0 for f in REQUIRED_MODEL):
            return folder
    raise RuntimeError('没有找到包含 vocabulary.txt 的完整 Whisper-small 模型；旧版本若可用，请勿删除。')


def remove_old_runtime(directory: Path):
    # Previously MSVCP140.dll 14.16 caused a c0000005 crash on this machine.
    # Use installed supported Microsoft VC++ x64 runtime rather than ship a
    # potentially incompatible version of the generic MSVCP140.dll.
    lib = directory / '_internal' / 'MSVCP140.dll'
    if lib.is_file():
        lib.unlink()
        report('已去除 _internal/MSVCP140.dll；新版需要系统安装受支持的 VC++ x64 运行库。')


def build():
    LOG.write_text('VideoToGPT 0.3.2 Windows release build\n', encoding='utf-8')
    if os.name != 'nt':
        raise RuntimeError('Windows EXE 必须在 Windows 上构建；此程序不修改当前旧版。')
    report('检查 Python/第三方库')
    run([sys.executable, '-c', 'import faster_whisper, av, ctranslate2, playwright, PyInstaller'])
    tools = find_tools()
    model = find_model()
    report('已找到模型：' + str(model))
    report('构建统一桌面 EXE')
    arguments = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--onedir', '--windowed', '--name', 'VideoToGPT',
        '--distpath', STAGE / 'dist', '--workpath', STAGE / 'work',
        '--specpath', STAGE,
        '--collect-all', 'playwright',
        '--collect-submodules', 'faster_whisper',
        '--collect-submodules', 'ctranslate2',
        '--collect-submodules', 'av',
        '--collect-submodules', 'onnxruntime',
        '--collect-submodules', 'tokenizers',
        '--hidden-import', 'playwright.sync_api',
        '--hidden-import', 'xhs_comments',
        '--hidden-import', 'content_extract',
        '--hidden-import', 'video_resolver',
        '--hidden-import', 'transcribe_portable',
        HERE / 'VideoToGPT.py']
    run(arguments)
    if not (WORK / 'VideoToGPT.exe').is_file():
        raise RuntimeError('PyInstaller 完成，但未找到 EXE。')
    (WORK / 'tools').mkdir(parents=True, exist_ok=True)
    for name, src in tools.items():
        dst = WORK / 'tools' / f'{name}.exe'
        shutil.copy2(src, dst)
        run([dst, '-version' if name in ('ffmpeg', 'ffprobe') else '--version'], cwd=WORK)
    (WORK / 'models' / 'small').mkdir(parents=True, exist_ok=True)
    for f in REQUIRED_MODEL:
        shutil.copy2(model / f, WORK / 'models' / 'small' / f)
    remove_old_runtime(WORK)
    shutil.copy2(HERE / '使用说明.txt', WORK / '使用说明.txt')
    shutil.copy2(HERE / '第三方组件说明.txt', WORK / '第三方组件说明.txt')
    (WORK / 'output').mkdir(exist_ok=True)
    # Build self-check uses bundled model + native modules, not source venv.
    check_file = WORK / 'portable_selfcheck.txt'
    report('运行发布版环境自检（不使用旧 .venv）')
    try:
        proc = subprocess.run([str(WORK / 'VideoToGPT.exe'), '--self-check',
                               str(check_file)], cwd=str(WORK), timeout=150)
        report('EXE 退出码：' + str(proc.returncode))
    except subprocess.TimeoutExpired:
        report('自检超时，可能有 native DLL 问题。')
        raise RuntimeError('发布版模型自检超时，已保留阶段文件以供排查。')
    if not check_file.is_file() or 'PASS' not in check_file.read_text(encoding='utf-8-sig'):
        raise RuntimeError('EXE 自检未通过。请发送 build_log.txt 和 .release_work/dist/VideoToGPT/portable_selfcheck.txt。')
    report('自检通过。复制到最终安装目录：' + str(TARGET))
    if TARGET.exists():
        raise RuntimeError(f'目标目录已存在：{TARGET}。为避免覆盖已有软件，请先移动或改名目标目录。')
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WORK, TARGET, ignore=shutil.ignore_patterns('portable_selfcheck.txt', '*.bak'))
    # User output is always relative to the independent executable.
    (TARGET / 'output').mkdir(exist_ok=True)
    archive = TARGET.parent / 'VideoToGPT_0.3.2_Windows_x64.zip'
    report('正在生成分享 ZIP（包含 Whisper 模型，文件可能较大）')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as z:
        for f in TARGET.rglob('*'):
            if f.is_file() and 'output' not in f.relative_to(TARGET).parts:
                z.write(f, Path('VideoToGPT') / f.relative_to(TARGET))
    report('完成：' + str(TARGET / 'VideoToGPT.exe'))
    report('分享包：' + str(archive))
    report('提醒：发送给别人前请检查第三方组件许可证；对方需 Windows x64、Edge 和 VC++ x64 运行库。')

if __name__ == '__main__':
    try:
        build()
    except Exception as exc:
        report('构建失败：' + str(exc))
        raise SystemExit(1)
