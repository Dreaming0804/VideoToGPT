# -*- coding: utf-8 -*-
"""Generic, public-video discovery without site-specific scraping or login bypass.

First ask yt-dlp's own (including GenericIE) extractor. If that cannot find a
video on an unknown HTML page, try a small, *bounded* collection of explicit
HTML5/OG/JSON-LD/iframe video sources with yt-dlp again. Never assume that a
page with text has no video. A URL being 'extractable' is verified by the media
metadata returned by yt-dlp, not simply the presence of an <iframe> or <video>.
"""
from __future__ import annotations
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from pathlib import Path
import json
import re
import subprocess

from content_extract import ensure_public_url, fetch, decode_html, ExtractionError, likely_video_site

MEDIA_SUFFIXES = ('.mp4', '.m4v', '.webm', '.mov', '.mkv', '.flv', '.m3u8', '.mpd')
_VIDEO_META = {'og:video', 'og:video:url', 'og:video:secure_url',
               'twitter:player:stream', 'twitter:player:stream:url'}
MAX_CANDIDATES = 4


@dataclass
class VideoSource:
    # Keep the user-facing source distinct from an embedded player/media URL.
    source_url: str
    media_url: str
    method: str
    title: str = ''
    description: str = ''
    extractor: str = ''
    referer: str = ''


def _clean_url(raw: str, base: str) -> str | None:
    raw = str(raw or '').strip().replace('&amp;', '&')
    if not raw or raw.startswith(('javascript:', 'data:', 'blob:', '#')):
        return None
    url = urljoin(base, raw)
    try:
        ensure_public_url(url)
    except (ExtractionError, ValueError):
        return None
    return url


def _media_type(info: object) -> bool:
    """A real format or a direct-media URL, not an empty playlist or title."""
    if not isinstance(info, dict):
        return False
    if info.get('is_live') or info.get('live_status') in ('is_live', 'is_upcoming'):
        return False  # VOD only in v0.3.2
    if info.get('formats') or info.get('requested_formats'):
        return True
    url = info.get('url') or ''
    if not isinstance(url, str):
        return False
    return urlsplit(url).path.lower().endswith(MEDIA_SUFFIXES)


def _selected_video(info: object) -> dict | None:
    if not isinstance(info, dict):
        return None
    if info.get('_type') in ('playlist', 'multi_video'):
        # One video per input link. A multi-item page selects only its first
        # confirmed playable entry, which is clearly reported to the user.
        entries = info.get('entries') or []
        for entry in entries[:5]:
            candidate = _selected_video(entry)
            if candidate is not None:
                return candidate
        return None
    return info if _media_type(info) else None


def check_ytdlp(url: str, yt: Path, *, timeout: int = 25) -> tuple[dict | None, str]:
    """Metadata-only video probe. No cookies, password, downloads, or login."""
    ensure_public_url(url)
    cmd = [str(yt), '--ignore-config', '--no-playlist', '--playlist-items', '1',
           '--skip-download', '--dump-single-json', '--no-warnings',
           '--socket-timeout', '8', '--retries', '1', '--extractor-retries', '1',
           '--no-progress', '--', url]
    try:
        opts = dict(capture_output=True, text=True, encoding='utf-8',
                    errors='replace', timeout=timeout)
        if hasattr(subprocess, 'CREATE_NO_WINDOW'):
            opts['creationflags'] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, **opts)
    except subprocess.TimeoutExpired:
        return None, '视频元数据探测超时'
    except (OSError, ValueError) as exc:
        return None, f'视频探测程序未能启动：{exc}'
    if result.returncode:
        # Never echo full stderr to the package: could contain signed URLs.
        return None, f'yt-dlp 暂未找到可下载的视频（退出码 {result.returncode}）'
    try:
        info = json.loads(result.stdout)
    except (ValueError, TypeError):
        return None, '视频元数据无法解析'
    selected = _selected_video(info)
    if selected is None:
        return None, '没有找到已确认可播放的视频格式（也可能是直播或多视频页）'
    return selected, ''


class _VideoHTML(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.candidates: list[tuple[str, str]] = []
        self.title = ''
        self.description = ''
        self._title = False
        self._json_ld = False
        self._scripts: list[str] = []
        self._script_buf: list[str] = []

    def _add(self, url: str, kind: str):
        target = _clean_url(url, self.base)
        if target and all(item[0] != target for item in self.candidates):
            self.candidates.append((target, kind))

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == 'title':
            self._title = True
        elif tag == 'meta':
            key = (attr.get('property') or attr.get('name') or '').lower()
            value = attr.get('content') or ''
            if key in _VIDEO_META and value:
                self._add(value, '网页视频元数据')
            elif key in ('description', 'og:description') and not self.description:
                self.description = value[:1500]
        elif tag == 'video':
            for key in ('src', 'data-src'):
                if attr.get(key):
                    self._add(attr[key], 'HTML5 视频')
        elif tag == 'source':
            src = attr.get('src') or attr.get('data-src')
            if src and (str(attr.get('type', '')).startswith('video/') or
                        urlsplit(src).path.lower().endswith(MEDIA_SUFFIXES)):
                self._add(src, 'HTML5 source')
        elif tag == 'iframe':
            src = attr.get('src', '')
            candidate = _clean_url(src, self.base)
            if candidate and (likely_video_site(candidate) or
                              any(piece in urlsplit(candidate).path.lower()
                                  for piece in ('/embed/', '/player/', '/video/'))):
                self._add(candidate, '嵌入式播放器')
        elif tag == 'script' and attr.get('type', '').lower() == 'application/ld+json':
            self._json_ld = True
            self._script_buf = []

    def handle_data(self, data):
        if self._title:
            self.title += data
        if self._json_ld and sum(map(len, self._script_buf)) < 200000:
            self._script_buf.append(data)

    def handle_endtag(self, tag):
        if tag == 'title':
            self._title = False
        if tag == 'script' and self._json_ld:
            self._scripts.append(''.join(self._script_buf))
            self._script_buf = []
            self._json_ld = False

    def find_jsonld(self):
        # JSON-LD can describe an article as well as a video. Only VideoObject
        # media links qualify, and they still go through a yt-dlp media probe.
        for piece in self._scripts:
            try:
                obj = json.loads(piece)
            except (ValueError, TypeError):
                continue
            self._walk_jsonld(obj)

    def _walk_jsonld(self, obj, depth=0):
        if depth > 5:
            return
        if isinstance(obj, list):
            for x in obj[:25]:
                self._walk_jsonld(x, depth+1)
        elif isinstance(obj, dict):
            types = obj.get('@type') or ''
            if isinstance(types, str):
                types = [types]
            if any(t.lower().endswith('videoobject') for t in types if isinstance(t, str)):
                for key in ('contentUrl', 'embedUrl'):
                    value = obj.get(key)
                    if isinstance(value, str):
                        self._add(value, 'VideoObject')
            if obj.get('@graph'):
                self._walk_jsonld(obj['@graph'], depth+1)


def discover_video(url: str, yt: Path, log=lambda s: None,
                   *, fetch_html=True) -> tuple[VideoSource | None, list[str]]:
    """Try yt-dlp first, then explicit embedded media sources from public HTML.

    Output is not a success guarantee: the later download may still be denied.
    """
    ensure_public_url(url)
    notes: list[str] = []
    direct, err = check_ytdlp(url, yt)
    if direct:
        kind = 'yt-dlp 通用解析'
        if direct.get('extractor_key') and direct.get('extractor_key') != 'Generic':
            kind = 'yt-dlp 平台解析'
        # If Generic returned a playable playlist entry, prefer that entry URL
        # rather than rerunning a playlist download from the host page.
        target = direct.get('webpage_url') or direct.get('original_url') or url
        target = _clean_url(target, url) or url
        notes.append('已由 yt-dlp 确认视频格式')
        if target != url:
            notes.append('页面包含多个媒体项：选择已确认的第一条视频')
        return VideoSource(url, target, kind,
                           title=str(direct.get('title') or '')[:500],
                           description=str(direct.get('description') or '')[:4000],
                           extractor=str(direct.get('extractor_key') or '')[:100],
                           referer=url if target != url else ''), notes
    notes.append(err)
    if not fetch_html:
        return None, notes
    try:
        data, final, content_type = fetch(url)
        if not ('html' in content_type.lower() or data.lstrip().lower().startswith((b'<!doctype', b'<html'))):
            return None, notes
        document = decode_html(data, content_type)
    except ExtractionError as exc:
        notes.append('网页视频标记探测失败：' + str(exc)[:180])
        return None, notes
    parser = _VideoHTML(final)
    try:
        parser.feed(document)
        parser.find_jsonld()
    except Exception as exc:
        notes.append('网页视频标记解析异常：' + str(exc)[:120])
    if parser.candidates:
        log(f'发现 {len(parser.candidates)} 个视频线索；验证前 {min(len(parser.candidates), MAX_CANDIDATES)} 个。')
    for candidate, method in parser.candidates[:MAX_CANDIDATES]:
        info, error = check_ytdlp(candidate, yt, timeout=12)
        if info is None:
            notes.append(f'{method}：未确认可用视频（{error}）')
            continue
        target = _clean_url(info.get('webpage_url') or candidate, candidate) or candidate
        return VideoSource(url, target, method, title=str(info.get('title') or parser.title)[:500],
                           description=str(info.get('description') or parser.description)[:4000],
                           extractor=str(info.get('extractor_key') or '')[:100],
                           referer=final), notes
    return None, notes
