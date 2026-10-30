# -*- coding: utf-8 -*-
"""Offline regression tests for generic video discovery (no real site requests)."""
import json
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent))
import video_resolver as vr
import VideoToGPT as app
from content_extract import ExtractionError, Page

YT=Path('/madeup/yt-dlp.exe')
VIDEO={'id':'abc','extractor_key':'Generic','title':'An embedded film',
       'webpage_url':'https://vid.example/player/1',
       'formats':[{'format_id':'x','url':'https://cdn.example.com/movie.mp4','vcodec':'avc1'}]}

# 1: yt-dlp generic can find a stream in a normal news/educational webpage.
with patch.object(vr, 'check_ytdlp', return_value=(VIDEO, '')):
    found, details=vr.discover_video('https://news.example.org/story',YT)
    assert found and found.source_url=='https://news.example.org/story'
    assert found.media_url=='https://vid.example/player/1' and found.referer
    assert found.title=='An embedded film'

# 2: HTML5 <source> fallback when yt-dlp cannot identify the article itself.
html=b'''<!doctype html><html><head><title>A study</title></head>
<body><p>A full text article</p><video controls><source src="/streams/live.m3u8" type="application/vnd.apple.mpegurl"></video></body></html>'''
# source type application/vnd.apple.mpegurl should still be discovered because m3u8 suffix.
def check_html(url, yt, timeout=25):
    if url.endswith('.m3u8'):
        return {'formats':[{'url':url,'vcodec':'h264'}],'webpage_url':url,'title':'An embedded study'},''
    return None, 'generic failed'
with patch.object(vr, 'check_ytdlp', side_effect=check_html), patch.object(vr, 'fetch', return_value=(html,'https://news.example.com/study','text/html; charset=utf-8')):
    found, notes=vr.discover_video('https://news.example.com/study',YT)
    assert found and found.media_url=='https://news.example.com/streams/live.m3u8'
    assert found.method=='HTML5 source'

# 3: HTML5 raw src, OG video, iframe and JSON-LD are all extracted as candidates.
p=vr._VideoHTML('https://news.example.org/articles/a')
p.feed('''<html><head><meta property="og:video" content="/trailer.mp4" />
<script type="application/ld+json">{"@type":"VideoObject","contentUrl":"https://cdn.example.com/film.mp4"}</script>
</head><body><iframe src="https://www.youtube.com/embed/xyz"></iframe><video src="/clip.webm"></video></body></html>''')
p.find_jsonld()
assert set(url for url,method in p.candidates)=={
    'https://news.example.org/trailer.mp4','https://cdn.example.com/film.mp4',
    'https://www.youtube.com/embed/xyz','https://news.example.org/clip.webm'}

# 4: reject unverified HTML links, and reject local-network link candidates.
with patch.object(vr, 'check_ytdlp', return_value=(None, 'no media')), patch.object(vr, 'fetch', return_value=(b'<html><body>News</body></html>','https://site.example.org/story','text/html')):
    found, _=vr.discover_video('https://site.example.org/story',YT)
    assert found is None
assert vr._clean_url('http://127.0.0.1:9999/private', 'https://site.example.org') is None

# 5: reject media-free playlist; choose an actual playable entry when present.
assert vr._selected_video({'_type':'playlist','entries':[{'title':'just title'}]}) is None
assert vr._selected_video({'_type':'playlist','entries':[{'title':'text'},{'formats':[{'url':'https://cdn.example.com/a.mp4'}]}]})

# 6: routing does not lose XHS image notes; unknown-site video takes priority.
with tempfile.TemporaryDirectory() as tmp:
    app.OUTPUT=Path(tmp)
    def fake_video(url, language, fps, emit, **kwargs):
        job=Path(tmp)/('video_'+str(len(list(Path(tmp).iterdir()))))
        job.mkdir()
        (job/'source_url.txt').write_text(kwargs.get('original_url') or url)
        (job/'video_source_info.json').write_text('{}')
        return job,job/'UploadToChatGPT.zip'
    def fake_page(page, emit):
        job=Path(tmp)/('page_'+str(len(list(Path(tmp).iterdir()))))
        job.mkdir()
        (job/'page_content.txt').write_text(page.body or page.title)
        return job,job/'UploadToChatGPT.zip'
    with patch.object(app, 'process_video', side_effect=fake_video),patch.object(app, 'process_page', side_effect=fake_page),patch.object(app,'probe', return_value=Page('https://www.xiaohongshu.com/explore/123','image_note',title='图文',body='正文')):
        job,z=app.process('https://www.xiaohongshu.com/explore/123','auto',1,lambda x:None)
        import zipfile
        with zipfile.ZipFile(z) as f:
            assert 'page_content.txt' in f.namelist() and 'video_source_info.json' not in f.namelist()
    src=vr.VideoSource('https://novel.example.org/story','https://cdn.example.com/a.mp4','OG video')
    with patch.object(app, 'process_video', side_effect=fake_video),patch.object(app, 'discover_video', return_value=(src,['ok'])):
        job,z=app.process('https://novel.example.org/story','auto',1,lambda x:None)
        with zipfile.ZipFile(z) as f:
            assert 'video_source_info.json' in f.namelist()

# 7: video with no audio still creates frames, manifest and uploadable archive.
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    (root/'models/small').mkdir(parents=True)
    (root/'models/small/model.bin').write_bytes(b'model fixture')
    app.MODEL=root/'models/small'
    app.OUTPUT=root/'output'
    app.TOOLS=root/'tools'
    def fake_bin(name): return root/'tools'/(name+'.exe')
    def fake_cmd(args, cwd, env, log):
        a=' '.join(map(str,args))
        if ' -x ' in a:
            raise RuntimeError('stream contains no audio')
        if '-vf' in args:
            path=Path(args[-1].replace('%06d','000000').replace('%03d','001'))
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'fake jpg')
        elif '-o' in args:
            (cwd/'video.mp4').write_bytes(b'fake MP4')
    with patch.object(app,'bin_path', side_effect=fake_bin),patch.object(app,'run_cmd',side_effect=fake_cmd):
        source=vr.VideoSource('https://journal.example.com/lecture','https://cdn.example.com/a.mp4','HTML5 video',title='Lecture')
        job,z=app.process_video(source.media_url,'auto',1.0,lambda x:None,original_url=source.source_url,source=source)
        assert (job/'README_分析说明.txt').is_file()
        assert (job/'video_source_info.json').is_file()
        assert '无音频' in (job/'README_分析说明.txt').read_text('utf-8-sig')
        assert (job/'source_url.txt').read_text()=='https://journal.example.com/lecture\n'
print('PASS: generic video discovery, HTML5/OG/JSON-LD/iframe, XHS image note, silent video, package')
