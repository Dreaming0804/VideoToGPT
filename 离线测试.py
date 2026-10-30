# -*- coding: utf-8 -*-
from pathlib import Path
import json
import sys
import tempfile
import zipfile
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parent))
import VideoToGPT as app
from xhs_comments import parse_comment_response, normalize_comment, completeness, report_stats, save_result

NOTE = '6ae08262000000002b03cd63'
URL = 'https://www.xiaohongshu.com/explore/' + NOTE
records = {
 'success': True,
 'data': {'comments': [
    {'id':'p1','content':'首条一级评论','sub_comment_count':2,'user_info':{'nickname':'用户A'},
     'sub_comments':[{'id':'c1','root_comment_id':'p1','content':'回复1','user_info':{'nickname':'用户B'}}]},
    {'id':'p2','content':'又一条一级评论','sub_comment_count':0,'user_info':{'nickname':'用户C'}}],
          'has_more': False,'cursor':'end'}
}
uri='https://edith.xiaohongshu.com/api/sns/web/v2/comment/page?note_id='+NOTE
parsed=parse_comment_response(uri,records,NOTE)
assert parsed and parsed[1]=='root' and len(parsed[0])==3
obs={'root':[parsed[2]],'sub':{}}
co=completeness(parsed[0],obs,True,'test')
assert not co['root_end_observed'] is False
assert 'p1' in co['reported_reply_gaps']
assert co['status'].startswith('部分')
reply={'data':{'comments':[{'id':'c2','root_comment_id':'p1','content':'回复2'}], 'has_more':False}}
parsed_reply=parse_comment_response('https://edith.xiaohongshu.com/api/sns/web/v2/comment/sub/page?note_id='+NOTE+'&root_comment_id=p1', reply, NOTE)
assert parsed_reply and parsed_reply[1]=='sub'
rows=parsed[0]+parsed_reply[0]
co=completeness(rows, {'root':[parsed[2]],'sub':{'p1':[parsed_reply[2]]}},True,'test')
assert co['status'].startswith('本次浏览会话分页结束')
assert not co['reported_reply_gaps']
assert parse_comment_response(uri, records, '0'*24) is None

with tempfile.TemporaryDirectory() as d:
  root=Path(d)
  a=save_result(URL,{r['id']:r for r in rows},{'root':[parsed[2]],'sub':{}},True,'test',[],root)
  assert (root/'comment_coverage.json').is_file()
  assert len(json.loads((root/'comments.json').read_text('utf-8-sig')))==4
  app.OUTPUT=root
  def fake_page(page,emit):
    job=root/'xhs_page_test';job.mkdir()
    (job/'page_content.txt').write_text('正文',encoding='utf-8')
    (job/'source_url.txt').write_text(page.url,encoding='utf-8')
    return job,job/'UploadToChatGPT.zip'
  def fake_video(url,lang,fps,emit,**kw):
    job=root/'xhs_video_test';job.mkdir()
    (job/'transcript.txt').write_text('英语转录',encoding='utf-8')
    (job/'source_url.txt').write_text(url,encoding='utf-8')
    return job,job/'UploadToChatGPT.zip'
  def _set():
    import threading
    e=threading.Event();e.set();return e
  def fake_collect(url,stop,scroll,callback,target_dir):
    assert scroll.is_set(), '需要默认自动滚动'
    (target_dir/'comments.txt').write_text('评论',encoding='utf-8')
    (target_dir/'comments.json').write_text('[]',encoding='utf-8')
    (target_dir/'comment_coverage.json').write_text('{}'.format('{}'),encoding='utf-8')
    callback(('count',1))
    callback(('coverage',{'status':'部分采集或无法验证完整性'}))
    callback(('done',(str(target_dir),'',1)))
  app.process_page=fake_page
  app.process_video=fake_video
  app.collect_xhs_comments=fake_collect
  import threading
  for kind in ('image_note','video'):
    app.probe=lambda url: SimpleNamespace(kind=kind,url=url)
    if kind=='image_note':
      job,archive=app.process(URL,'auto',1.0,lambda x:None,True,threading.Event(),_set())
    else:
      job,archive=app.process(URL,'auto',1.0,lambda x:None,True,threading.Event(),_set())
    with zipfile.ZipFile(archive) as z:
      assert 'comments.txt' in z.namelist()
      assert 'comment_coverage.json' in z.namelist()
      assert ('page_content.txt' if kind=='image_note' else 'transcript.txt') in z.namelist()
    print('PASS',kind,archive.name)
print('全部离线测试通过')

# 0.3.1 regression coverage: no login unless specifically selected.
from content_extract import extract_url, probe, Page
import threading
# Default UI configuration is not initialized in CLI tests; source is guarded.
assert 'value=False' in (Path(__file__).parent/'VideoToGPT.py').read_text(encoding='utf-8')
# Direct-video links bypass ordinary-page HTML extraction.
assert probe('https://example.org/public/demo.mp4').kind == 'video'
assert probe('https://example.org/stream/master.m3u8').kind == 'video'
# Output directory is configurable via a persistent user preference.
with tempfile.TemporaryDirectory() as d:
    dest = Path(d) / 'output'
    orig_settings = app.SETTINGS_FILE
    orig_output = app.OUTPUT
    try:
        app.SETTINGS_FILE = Path(d) / 'settings.json'
        app.set_output_dir(dest)
        assert app.OUTPUT == dest
        assert json.loads(app.SETTINGS_FILE.read_text('utf-8'))['output_directory'] == str(dest)
    finally:
        app.SETTINGS_FILE = orig_settings
        app.OUTPUT = orig_output
print('0.3.1 附加离线测试通过：评论可选 / 直链视频 / 输出位置设置')
