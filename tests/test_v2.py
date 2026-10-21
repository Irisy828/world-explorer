import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
import collector as app
import editorial as e
import persistence as store

class V2Tests(unittest.TestCase):
 def setUp(self):
  Path('work').mkdir(exist_ok=True)
  self.now=datetime(2026,10,21,8,tzinfo=app.CST)
  self.row=dict(video_id='TEST0000001',title='Science explained experiment',description='',channel='test',published_at='2026-10-19T00:00:00Z',view_count=200,regions=['US'],url='https://www.youtube.com/watch?v=TEST0000001')
 def baseline(self,root,hours=24,count=100,demo=False):
  (root/'history').mkdir(exist_ok=True)
  (root/'history/2026-10-20.json').write_text(json.dumps(dict(collected_at=(self.now-timedelta(hours=hours)).isoformat(),demo=demo,videos=[dict(self.row,view_count=count)])),encoding='utf-8')
 def test_delta_valid_and_missing(self):
  with tempfile.TemporaryDirectory(dir='work') as d:
   root=Path(d)
   self.assertIsNone(e.delta(self.row,root,self.now)[0])
   self.baseline(root)
   self.assertEqual(e.delta(self.row,root,self.now)[:2],(100,24))
   for count in (None,250,-1):
    self.baseline(root,count=count)
    self.assertIsNone(e.delta(self.row,root,self.now)[0])
 def test_delta_reject_stale_demo_and_after_publication(self):
  with tempfile.TemporaryDirectory(dir='work') as d:
   root=Path(d)
   for hours,demo in [(48,False),(24,True),(-2,False),(1,False)]:
    self.baseline(root,hours=hours,demo=demo)
    self.assertIsNone(e.delta(self.row,root,self.now)[0])
   self.baseline(root)
   for pub in ['bad','2026-10-20T12:00:00Z','2026-10-22T00:00:00Z']:
    self.assertIsNone(e.delta(dict(self.row,published_at=pub),root,self.now)[0])
 def test_diversity_and_no_invented_filler(self):
  rows=[dict(self.row,video_id=f'TEST{i:07}') for i in range(20)]
  selected=e.enrich(rows,Path('work/absent'),self.now,{},True)
  self.assertEqual(len(selected),3)
  self.assertTrue(all(r['view_delta'] is None for r in selected))
  self.assertEqual(e.enrich([dict(self.row,title='music',description='')],Path('work'),self.now,{}),[])
 def test_ten_and_topic_market_caps(self):
  rows=[]
  for i,(topic,words) in enumerate(e.TOPICS.items()):
   for j in range(5):
    rows.append(dict(self.row,video_id=f'TEST{i:03}{j:04}',title=words[0],regions=['US','JP','GB','AU']))
  rows=e.enrich(rows,Path('work/absent'),self.now,{},True)
  self.assertEqual(len(rows),10)
  self.assertLessEqual(max(e.Counter(r['topic'] for r in rows).values()),3)
  self.assertLessEqual(max(e.Counter(r['selection_market'] for r in rows).values()),3)
 def test_discovery_quota_and_hydration(self):
  calls=[]
  def fetch(p,key,endpoint):
   calls.append((endpoint,p))
   if endpoint=='search':return {'items':[{'id':{'videoId':'TEST0000001'}}]}
   return {'items':[{'id':'TEST0000001','status':{'privacyStatus':'public'},'snippet':{'title':'science explained','publishedAt':self.row['published_at']},'statistics':{'viewCount':'200'}}]}
  c=app.config('config.json')
  rows=e.discover(c,'',self.now,fetch)
  self.assertEqual(len(calls),2*len(c['regions']))
  self.assertEqual(len(rows),1)
  self.assertEqual(rows[0]['regions'],c['regions'])
 def test_review_exports_and_retention(self):
  with tempfile.TemporaryDirectory(dir='work') as d:
   root=Path(d);rows=e.enrich([dict(self.row,title='=science explained | [x]')],root,self.now,{},True)
   e.report(root,rows,self.now,True)
   csv=(root/'reports/2026-10-21-review.csv').read_text(encoding='utf-8-sig')
   self.assertIn("'=science",csv)
   self.assertIn('&#124;',(root/'reports/2026-10-21-review.md').read_text(encoding='utf-8'))
   old=root/'reports/2026-09-01-review.csv';old.write_text('old')
   app.cleanup(root,self.now,25);self.assertFalse(old.exists())
 def test_upload_failure_preserves_old_asset(self):
  with tempfile.TemporaryDirectory(dir='work') as d, patch.dict('os.environ',GH_REPO='a/b',GITHUB_RUN_ID='1'), patch('persistence.release',return_value={'assets':[]}), patch('persistence.gh',side_effect=RuntimeError('failed')) as gh:
   with self.assertRaises(RuntimeError):store.save(Path(d))
   self.assertEqual(gh.call_count,1)
   self.assertEqual(gh.call_args.args[:2],('release','upload'))
 def test_restore_rejects_unsafe_members(self):
  import io,zipfile
  buf=io.BytesIO()
  with zipfile.ZipFile(buf,'w') as z:z.writestr('../secret','x')
  with tempfile.TemporaryDirectory(dir='work') as d,patch.dict('os.environ',GH_REPO='a/b'),patch('persistence.release',return_value={'assets':[{'id':1,'name':'snapshot-1.zip','size':len(buf.getvalue()),'created_at':'2026'}]}),patch('persistence.gh',return_value=buf.getvalue()):
   with self.assertRaises(RuntimeError):store.restore(Path(d))

if __name__=='__main__':unittest.main()
