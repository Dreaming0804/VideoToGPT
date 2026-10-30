# -*- coding: utf-8 -*-
"""Conservative public-web page extractor for VideoToGPT.

Uses only Python stdlib. Does not attempt to defeat logins/captchas,
access private material, or acquire account cookies.
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36")
MAX_HTML = 7 * 1024 * 1024
MAX_IMG = 12 * 1024 * 1024
MAX_IMAGES = 20
VIDEO_HOSTS = ('bilibili.com', 'b23.tv', 'youtube.com', 'youtu.be',
               'vimeo.com', 'douyin.com', 'tiktok.com', 'ixigua.com',
               'acfun.cn', 'kuaishou.com')


class ExtractionError(RuntimeError):
    pass


@dataclass
class Page:
    url: str
    kind: str  # image_note, article, video
    title: str = ''
    body: str = ''
    author: str = ''
    image_urls: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def extract_url(text: str) -> str:
    """Accept plain URLs and app share text containing a single URL."""
    m = re.search(r'https?://[^\s<>"\'，。！；：）】\u300a\u300b]+', text)
    if not m:
        raise ExtractionError('未找到 http/https 链接。请粘贴完整分享文案或网页地址。')
    url = m.group(0).rstrip('.,;!，。；！）)]}')
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise ExtractionError('链接格式有误。')
    return url


def domain_is(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith('.' + suffix)


def is_xhs(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    return domain_is(host, 'xiaohongshu.com') or domain_is(host, 'xhslink.com')


def likely_video_site(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    return any(domain_is(host, s) for s in VIDEO_HOSTS)


def ensure_public_url(url: str, trusted_image=False) -> None:
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ExtractionError('仅支持正常的 HTTP/HTTPS 公共网页。')
    hostname = p.hostname.lower()
    if hostname in ('localhost', 'localhost.localdomain'):
        raise ExtractionError('禁止访问本地服务地址。')
    try:
        ip = ipaddress.ip_address(hostname)
        if not ip.is_global:
            raise ExtractionError('禁止访问局域网或保留 IP 地址。')
    except ValueError:
        # Use DNS for safety on redirects to IPv4/IPv6 private address. No
        # security guarantee against malicious DNS rebinding; local desktop only.
        if not trusted_image and hostname.endswith(('.local', '.internal')):
            raise ExtractionError('禁止访问内部网络域名。')


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ensure_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, ua=DESKTOP_UA, limit=MAX_HTML, referer=None, accepted=None) -> tuple[bytes, str, str]:
    ensure_public_url(url)
    handler = urllib.request.build_opener(PublicRedirect())
    headers = {'User-Agent': ua, 'Accept': accepted or 'text/html,application/xhtml+xml,*/*;q=0.8'}
    if referer:
        headers['Referer'] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with handler.open(req, timeout=22) as r:
            final = r.geturl()
            ensure_public_url(final)
            content_type = r.headers.get('Content-Type', '')
            data = r.read(limit + 1)
            if len(data) > limit:
                raise ExtractionError('网页或图片超过安全大小上限，跳过。')
            return data, final, content_type
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        raise ExtractionError(f'网页访问失败：{e}') from e


def decode_html(data: bytes, content_type='') -> str:
    charset = re.search(r'charset\s*=\s*([\w-]+)', content_type, re.I)
    encodings = [charset.group(1) if charset else 'utf-8', 'utf-8', 'gb18030']
    for enc in dict.fromkeys(encodings):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('utf-8', 'replace')


def balanced_object(js: str, start: int) -> str | None:
    """Extract object literal while ignoring braces in quoted strings."""
    while start < len(js) and js[start].isspace(): start += 1
    if start >= len(js) or js[start] != '{': return None
    depth, quote, escape = 0, None, False
    for idx in range(start, min(len(js), start + MAX_HTML)):
        c = js[idx]
        if quote:
            if escape: escape = False
            elif c == '\\': escape = True
            elif c == quote: quote = None
            continue
        if c in ('"', "'"):
            quote = c
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return js[start:idx + 1]
    return None


def replace_js_literals(s: str) -> str:
    """Replace JS undefined/NaN outside strings (not in user content)."""
    out, quote, esc, i = [], None, False, 0
    while i < len(s):
        c = s[i]
        if quote:
            out.append(c)
            if esc: esc = False
            elif c == '\\': esc = True
            elif c == quote: quote = None
            i += 1
            continue
        if c in ('"', "'"):
            quote = c; out.append(c); i += 1; continue
        matched = False
        for token, repl in (('undefined', 'null'), ('NaN', 'null')):
            if s.startswith(token, i) and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] == '_')) and (i + len(token) == len(s) or not (s[i + len(token)].isalnum() or s[i + len(token)] == '_')):
                out.append(repl); i += len(token); matched = True; break
        if not matched: out.append(c); i += 1
    return ''.join(out)


def xhs_state(document: str) -> dict | None:
    for match in re.finditer(r'(?:window\.)?__INITIAL_STATE__\s*=\s*', document):
        raw = balanced_object(document, match.end())
        if not raw: continue
        raw = replace_js_literals(raw)
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            pass
    return None


def get_nested(d, *keys):
    for key in keys:
        if not isinstance(d, dict): return None
        d = d.get(key)
    return d


def best_xhs_note(state: dict, final_url: str) -> dict | None:
    note_root = state.get('note') or {}
    if not isinstance(note_root, dict): note_root = {}
    note_id = note_root.get('firstNoteId')
    detail_map = note_root.get('noteDetailMap') or {}
    items = []
    if isinstance(detail_map, dict):
        if note_id in detail_map: items.append(detail_map[note_id])
        items.extend(detail_map.values())
    for entry in items:
        if not isinstance(entry, dict): continue
        note = entry.get('note', entry)
        if isinstance(note, dict) and (note.get('imageList') or note.get('video') or note.get('desc') or note.get('title')):
            return note
    for keys in (('noteData', 'data', 'noteData'), ('noteData', 'noteData'), ('note', 'noteDetail')):
        note = get_nested(state, *keys)
        if isinstance(note, dict) and note:
            return note.get('note', note)
    return None


def normalize_url(value: str, base: str) -> str:
    value = html.unescape(value or '').replace('\\/', '/')
    if value.startswith('//'): value = 'https:' + value
    return urllib.parse.urljoin(base, value)


def image_url(obj: dict | str, base: str) -> str | None:
    if isinstance(obj, str):
        u = obj
    elif isinstance(obj, dict):
        u = obj.get('urlDefault') or obj.get('url') or obj.get('urlPre') or obj.get('urlOrigin')
        if not u:
            for entry in obj.get('infoList') or []:
                if isinstance(entry, dict) and entry.get('url'):
                    u = entry['url']
                    if entry.get('imageScene') in ('WB_DFT', 'WB_ORIGIN'):
                        break
        if isinstance(u, dict): u = u.get('url') or u.get('urlDefault')
    else:
        return None
    if not isinstance(u, str) or not u.strip(): return None
    u = normalize_url(u, base)
    if urllib.parse.urlsplit(u).scheme not in ('http', 'https'): return None
    return u


class WebTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ''
        self.meta = {}
        self.parts = []
        self.images = []
        self.depth_article = 0
        self.depth_main = 0
        self.nested = []
        self.ignore_depth = 0
        self.in_title = False
        self.capture = 0
        self.buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta':
            k = a.get('property') or a.get('name')
            if k and a.get('content'):
                self.meta[k.lower()] = a['content']
        if tag == 'title': self.in_title = True
        if tag in ('script','style','noscript','svg'):
            self.ignore_depth += 1
            return
        if self.ignore_depth:
            return
        if tag == 'article': self.depth_article += 1
        if tag == 'main': self.depth_main += 1
        if tag in ('p', 'h1', 'h2', 'h3', 'li', 'blockquote'):
            self.capture += 1
            self.buf = []
        if tag == 'img' and (self.depth_main or self.depth_article):
            candidate = a.get('data-src') or a.get('src') or ''
            if candidate: self.images.append(candidate)

    def handle_endtag(self, tag):
        if tag in ('script','style','noscript','svg'):
            self.ignore_depth = max(0,self.ignore_depth - 1)
            return
        if self.ignore_depth: return
        if tag == 'title': self.in_title = False
        if tag in ('p', 'h1', 'h2', 'h3', 'li', 'blockquote') and self.capture:
            value = ' '.join(''.join(self.buf).split())
            if value and (not self.parts or self.parts[-1] != value): self.parts.append(value)
            self.capture = max(0, self.capture - 1)
            self.buf = []
        if tag == 'article': self.depth_article = max(0,self.depth_article - 1)
        if tag == 'main': self.depth_main = max(0,self.depth_main - 1)

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if not self.ignore_depth and self.capture:
            self.buf.append(data)


def parsed_page(document: str) -> WebTextParser:
    p = WebTextParser()
    try: p.feed(document)
    except Exception: pass
    return p


def parse_xhs(document: str, url: str) -> Page:
    p = parsed_page(document)
    state = xhs_state(document)
    note = best_xhs_note(state, url) if state else None
    if note:
        title = str(note.get('title') or p.meta.get('og:title') or '').strip()
        desc = str(note.get('desc') or note.get('description') or p.meta.get('og:description') or '').strip()
        user = note.get('user') or {}
        author = str((user.get('nickname') or user.get('nickName') or '') if isinstance(user, dict) else '')
        # The explicit note type distinguishes a video note from an image note;
        # some video notes still have imageList (the cover).
        typ = str(note.get('type') or '').lower()
        is_video = typ == 'video' or bool(note.get('video'))
        images = []
        for img in note.get('imageList') or []:
            u = image_url(img, url)
            if u and u not in images: images.append(u)
        if is_video:
            return Page(url, 'video', title, desc, author, [])
        if images or (title and desc):
            warnings = [] if images else ['网页只提供了笔记文字，没有读取到图集。结果可能不完整。']
            return Page(url, 'image_note', title, desc, author, images[:MAX_IMAGES], warnings)
    title = (p.meta.get('og:title') or p.title or '').strip()
    desc = (p.meta.get('og:description') or p.meta.get('description') or '').strip()
    img = p.meta.get('og:image') or p.meta.get('twitter:image')
    if title in ('小红书', '小红书 - 你的生活指南', 'REDnote') and len(desc) < 25:
        raise ExtractionError('当前获取到的是小红书访问提示页，没有笔记正文。请在电脑浏览器确认该笔记可打开，尽量使用保留完整参数的分享链接。')
    if (p.meta.get('og:type') or '').lower().startswith('video'):
        return Page(url, 'video', title, desc)
    if title and desc and img and not any(x in (title + desc) for x in ('登录后', '打开小红书App')):
        return Page(url, 'image_note', title, desc, '', [normalize_url(img, url)], ['仅获得网页预览图，可能不是完整图集。'])
    raise ExtractionError('未找到可读取的小红书笔记正文或图片。可能需要登录、链接已失效或平台调整了网页结构。')


def parse_article(document: str, url: str) -> Page:
    p = parsed_page(document)
    title = (p.meta.get('og:title') or p.title or '').strip()
    description = (p.meta.get('og:description') or p.meta.get('description') or '').strip()
    body = '\n\n'.join(p.parts)
    if not body and description: body = description
    if len(body) < 25:
        raise ExtractionError('这个网页没有读取到足够的正文；可能是动态加载、登录页或视频页面。')
    imgs = []
    for u in p.images:
        u = normalize_url(u, url)
        if u not in imgs: imgs.append(u)
    if not imgs and p.meta.get('og:image'):
        imgs = [normalize_url(p.meta['og:image'], url)]
    return Page(url, 'article', title, body[:150000], '', imgs[:8])


def probe(link: str) -> Page:
    url = extract_url(link)
    if likely_video_site(url):
        return Page(url, 'video')
    # Direct file links are valid media inputs even without HTML pages.
    path = urllib.parse.urlsplit(url).path.lower()
    if path.endswith(('.mp4', '.webm', '.mkv', '.mov', '.m4v', '.m3u8', '.mpd')):
        return Page(url, 'video')
    if is_xhs(url):
        # Browsers sometimes display different documents based on User-Agent.
        # Prefer desktop, fall back to mobile when it yields no content.
        failures = []
        for ua in (DESKTOP_UA, MOBILE_UA):
            try:
                data, final, content_type = fetch(url, ua)
                if not is_xhs(final):
                    raise ExtractionError('短链接重定向到了其他网站，请检查分享链接。')
                if '/login' in urllib.parse.urlsplit(final).path:
                    raise ExtractionError('小红书需要登录后才能查看该笔记。')
                page = parse_xhs(decode_html(data, content_type), final)
                return page
            except ExtractionError as e:
                failures.append(str(e))
        raise ExtractionError('小红书解析失败：' + ' / '.join(failures[:2]))
    data, final, ctype = fetch(url)
    if 'text/html' not in ctype.lower() and not data.lstrip().startswith((b'<!DOCTYPE', b'<html', b'<HTML')):
        raise ExtractionError('这不是可解析的网页正文。')
    text = decode_html(data, ctype)
    p = parsed_page(text)
    if ((p.meta.get('og:type') or '').lower().startswith('video')
            or p.meta.get('og:video') or p.meta.get('og:video:url')
            or re.search(r'<video\b|<source\b[^>]+\btype=[\"\']video/|<iframe\b[^>]+(?:youtube|bilibili|vimeo)', text, re.I)):
        return Page(final, 'video')
    return parse_article(text, final)


def save_images(page: Page, folder: Path, emit) -> tuple[list[Path], list[str]]:
    """Download images with a hard upper bound, report failures explicitly."""
    folder.mkdir(parents=True, exist_ok=True)
    stored, errors = [], []
    seen = set()
    for i, u in enumerate(page.image_urls[:MAX_IMAGES], 1):
        if u in seen: continue
        seen.add(u)
        try:
            raw, final, mime = fetch(u, limit=MAX_IMG, referer=page.url, accepted='image/avif,image/webp,image/jpeg,image/png,image/*;q=0.8')
            content_type = mime.split(';')[0].strip().lower()
            # Some image CDNs return application/octet-stream for real images.
            # Check magic bytes before treating a response as downloadable media.
            if raw.startswith(b'\xff\xd8\xff'):
                ext = '.jpg'
            elif raw.startswith(b'\x89PNG\r\n\x1a\n'):
                ext = '.png'
            elif raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
                ext = '.webp'
            elif raw.startswith((b'GIF87a', b'GIF89a')):
                ext = '.gif'
            elif len(raw) >= 12 and raw[4:8] == b'ftyp' and b'avif' in raw[8:24]:
                ext = '.avif'
            else:
                raise ExtractionError(f'图片格式无法确认（Content-Type={content_type}）')
            if len(raw) < 300: raise ExtractionError('图片内容过小')
            target = folder / f'image_{i:02d}{ext}'
            target.write_bytes(raw)
            stored.append(target)
            emit(f'图片 {i}: {target.name}，{len(raw)/1048576:.2f} MiB')
        except (ExtractionError, OSError, ValueError) as e:
            errors.append(f'第{i}张图片：{e}')
            emit(f'跳过图片 {i}: {e}')
    return stored, errors
