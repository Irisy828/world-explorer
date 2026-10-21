import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import tracker as t
import persistence as p

NOW = datetime(2026, 10, 21, 6, tzinfo=timezone.utc)
A, B = 'aaaaaaaaaaa', 'bbbbbbbbbbb'


def row(vid=A, count=100, at=NOW, **extra):
    return dict(video_id=vid, view_count=count, observed_at=at.isoformat(),
        published_at=(NOW-timedelta(days=100)).isoformat(), title='=测试|<script>',
        channel='频道', category='10', sources=[], last_discovered_at=NOW.isoformat(),
        url='https://www.youtube.com/watch?v='+vid, **extra)


def hist(*rows):
    return [dict(schema=3, demo=False, collected_at=rows[0]['observed_at'], videos=list(rows))]


class TrackerTests(unittest.TestCase):
    def setUp(self):
        Path('work').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir='work')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.c = t.settings('tracker_config.json')

    def rank(self, current=None, previous=None, now=NOW):
        return t.ranking(current or [row()], previous or hist(row(count=50, at=NOW-timedelta(hours=24))), now)

    def test_delta_wins_over_lifetime_and_editorial_fields(self):
        top, excluded, total = self.rank([row(count=200), row(B, 10000)], hist(row(count=0, at=NOW-timedelta(hours=24)), row(B, 9999, NOW-timedelta(hours=24))))
        self.assertEqual([r['video_id'] for r in top], [A, B])
        self.assertEqual(top[0]['view_delta'], 200)
        self.assertEqual((excluded, total), ([], 2))

    def test_zero_and_tie_order(self):
        top, _, _ = self.rank([row(B, 0), row(count=0)], hist(row(B, 0, NOW-timedelta(hours=24)), row(count=0, at=NOW-timedelta(hours=24))))
        self.assertEqual([r['video_id'] for r in top], [A, B])
        self.assertEqual(top[0]['view_delta'], 0)

    def test_nearest_baseline_not_latest(self):
        h = hist(row(count=20, at=NOW-timedelta(hours=24)), row(count=30, at=NOW-timedelta(hours=23, minutes=40)))
        self.assertEqual(self.rank(previous=h)[0][0]['baseline_view_count'], 20)

    def test_window_boundaries_and_no_extrapolation(self):
        for minutes, expected in [(30, 1), (31, 0), (-30, 1), (-31, 0), (360, 0)]:
            with self.subTest(minutes=minutes):
                top, _, _ = self.rank(previous=hist(row(count=50, at=NOW-timedelta(hours=24, minutes=minutes))))
                self.assertEqual(len(top), expected)
                if top:
                    self.assertEqual(top[0]['view_delta'], 50)

    def test_endpoint_age(self):
        for minutes, expected in [(15, 1), (16, 0), (-1, 0)]:
            self.assertEqual(len(self.rank([row(at=NOW-timedelta(minutes=minutes))])[0]), expected)

    def test_same_calendar_day_baseline_allowed(self):
        now = NOW+timedelta(hours=18, minutes=10)
        old = row(count=50, at=now-timedelta(hours=23, minutes=50))
        self.assertEqual(len(self.rank([row(at=now)], hist(old), now)[0]), 1)

    def test_missing_counts_publication_and_baselines(self):
        for mutation in [dict(view_count=None), dict(view_count=True), dict(view_count=-1), dict(observed_at='bad'), dict(published_at=None), dict(published_at=(NOW+timedelta(hours=1)).isoformat()), dict(published_at=(NOW-timedelta(hours=6)).isoformat())]:
            with self.subTest(mutation=mutation):
                r=row();r.update(mutation)
                self.assertEqual(len(self.rank([r])[0]), 0)
        self.assertEqual(t.ranking([row()], [], NOW)[0], [])
        self.assertEqual(self.rank(previous=hist(row(count=None, at=NOW-timedelta(hours=24))))[0], [])

    def test_counter_correction_including_intermediate(self):
        self.assertEqual(self.rank(previous=hist(row(count=110, at=NOW-timedelta(hours=24))))[0], [])
        h = hist(row(count=50, at=NOW-timedelta(hours=24)), row(count=40, at=NOW-timedelta(hours=6)))
        self.assertEqual(self.rank(previous=h)[0], [])

    def test_history_demo_legacy_corruption(self):
        h=hist(row(count=50, at=NOW-timedelta(hours=24)))[0]
        path=self.root/'history'/'2026-10-20T060000Z.json'
        for data in [dict(h, demo=True), dict(h, schema=2)]:
            t.atomic_json(path, data)
            self.assertEqual(t.load_history(self.root, NOW), [])
        t.atomic_json(path, h)
        self.assertEqual(len(t.load_history(self.root, NOW)), 1)
        path.write_text('{broken')
        with self.assertRaises(ValueError):t.load_history(self.root, NOW)

    def test_multiple_runs_and_retention(self):
        t.atomic_json(self.root/'history/2026-10-14T060000Z.json', {})
        t.atomic_json(self.root/'history/2026-10-15T060000Z.json', {})
        t.cleanup(self.root, NOW, 7)
        self.assertFalse((self.root/'history/2026-10-14T060000Z.json').exists())
        self.assertTrue((self.root/'history/2026-10-15T060000Z.json').exists())

    def test_historical_video_tracked_after_leaving_discovery(self):
        members, dropped=t.pool(hist(row()), {}, NOW, self.c)
        self.assertIn(A, members)
        seen=[]
        def fetch(endpoint, params):
            seen.append(params['id'])
            return {'items':[{'id':A,'status':{'privacyStatus':'public'},'snippet':{},'statistics':{'viewCount':'123'}}]}
        sampled, missing=t.sample(members, fetch, lambda:NOW)
        self.assertEqual((seen, sampled[0]['view_count'], missing), ([A], 123, set()))

    def test_pool_expiry_capacity_and_source_dedup(self):
        stale=row();stale['last_discovered_at']=(NOW-timedelta(days=15)).isoformat()
        self.assertEqual(t.pool(hist(stale), {}, NOW, self.c)[0], {})
        source=dict(method='chart',region='US',category='all',discovered_at=NOW.isoformat())
        members, dropped=t.pool(hist(row()), {B:[source]}, NOW, dict(self.c,pool_limit=1))
        self.assertEqual((set(members), dropped), ({A}, 1))
        r=row();r['sources']=[source]
        members,_=t.pool(hist(r), {A:[source]}, NOW, self.c)
        self.assertEqual(len(members[A]['sources']), 1)

    def test_discovery_regions_categories_rotation_no_keywords(self):
        calls=[]
        def call(endpoint, params):
            calls.append((endpoint,params))
            if endpoint=='videoCategories':
                return {'items':[dict(id=str(i),snippet={'assignable':True}) for i in range(1,6)]}
            return {'items':[{'id': {'videoId': A} if endpoint=='search' else A}]}
        found=t.discover(self.c, call, NOW)
        self.assertEqual(len(found[A]), 18)
        searches=[p for e,p in calls if e=='search']
        self.assertEqual(len(searches),12)
        self.assertTrue(all('q' not in p and 'videoDuration' not in p for p in searches))
        categories={p['videoCategoryId'] for p in searches}
        calls.clear();t.discover(self.c,call,NOW+timedelta(hours=6))
        self.assertNotEqual(categories,{p['videoCategoryId'] for e,p in calls if e=='search'})

    def test_unavailable_removed_from_history_and_reports(self):
        rows,missing=t.sample({A:row()},lambda *args:{'items':[]},lambda:NOW)
        self.assertEqual((rows,missing),([], {A}))
        h=hist(row());t.atomic_json(self.root/'history/2026-10-20.json', h[0])
        (self.root/'reports').mkdir();(self.root/'reports/old.md').write_text(A)
        t.purge_unavailable(self.root,h,missing)
        self.assertEqual(h[0]['videos'],[])
        self.assertEqual(list((self.root/'reports').glob('*')),[])

    def test_report_fields_escape_and_sparse(self):
        top,excluded,total=self.rank()
        t.report(self.root,top,excluded,total,NOW,{})
        md=next((self.root/'reports').glob('*.md')).read_text(encoding='utf-8')
        self.assertIn('非全球前十',md);self.assertIn('&#124;',md);self.assertIn('&lt;script&gt;',md)
        csv=next((self.root/'reports').glob('*.csv')).read_text(encoding='utf-8-sig')
        self.assertIn("'=测试",csv)
        for field in ['baseline_at','view_delta','sources','window_error_minutes','url']:
            self.assertIn(field,csv)

    def test_complete_run_restart_offline(self):
        clock=[NOW]
        count=[50]
        def call(endpoint,params):
            if endpoint=='videoCategories':return {'items':[]}
            if 'chart' in params:return {'items':[{'id':A}]}
            return {'items':[{'id':A,'status':{'privacyStatus':'public'},'snippet':{'title':'music','publishedAt':(NOW-timedelta(days=100)).isoformat()},'statistics':{'viewCount':str(count[0])}}]}
        self.assertEqual(t.run(self.root,self.c,call,lambda:clock[0]),[])
        clock[0]+=timedelta(hours=24);count[0]=120
        top=t.run(self.root,self.c,call,lambda:clock[0])
        self.assertEqual(top[0]['view_delta'],70)
        self.assertEqual(len(list((self.root/'history').glob('*.json'))),2)

    def test_api_failure_no_snapshot_or_partial_report(self):
        def fail(*args):raise RuntimeError('failed')
        with self.assertRaises(RuntimeError):t.run(self.root,self.c,fail,lambda:NOW)
        self.assertEqual(list(self.root.rglob('*.json')),[])

    def test_api_headers_redaction_no_retries_and_ledger(self):
        api=t.API('SECRET',self.c,self.root)
        err=HTTPError('SECRET',403,'SECRET',{},None)
        with patch('tracker.urlopen',side_effect=err) as opened:
            with self.assertRaises(RuntimeError) as cm:api('search',{})
        req=opened.call_args.args[0]
        self.assertNotIn('SECRET',req.full_url)
        self.assertEqual(req.get_header('X-goog-api-key'),'SECRET')
        self.assertNotIn('SECRET',str(cm.exception))
        self.assertEqual(opened.call_count,1)
        ledger=json.loads(next((self.root/'history').glob('*-quota.json')).read_text())
        self.assertEqual(len(ledger['attempts']),1)

    def test_rolling_quota_guard_survives_restart(self):
        now=datetime.now(timezone.utc)
        t.atomic_json(self.root/'history'/(now.strftime('%Y-%m-%d')+'-quota.json'), {'attempts':[dict(at=now.isoformat(),endpoint='search')]*80})
        with patch('tracker.urlopen') as opened:
            with self.assertRaises(RuntimeError):t.API('SECRET',self.c,self.root)('search',{})
            opened.assert_not_called()

    def test_approval_gate_before_api(self):
        with patch.dict('os.environ',{'YOUTUBE_API_KEY':'SECRET','YOUTUBE_ANALYTICS_APPROVAL_CONFIRMED':''}), patch('sys.argv',['tracker']), patch('tracker.urlopen') as opened:
            with self.assertRaises(ValueError):t.main()
            opened.assert_not_called()

    def test_persistence_old_asset_retained_on_upload_failure(self):
        with patch('persistence.release', return_value={'assets':[{'id':1,'name':'snapshot-old.zip'}]}), patch.dict('os.environ',{'GITHUB_RUN_ID':'offline'}), patch('persistence.gh',side_effect=RuntimeError('upload failed')) as gh:
            with self.assertRaises(RuntimeError):p.save(self.root)
            self.assertEqual(gh.call_count,1)
            self.assertEqual(gh.call_args.args[:2],('release','upload'))

    def test_restore_rejects_path_escape(self):
        import zipfile
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('history/../../escape.json','{}')
        with patch('persistence.release',return_value={'assets':[{'id':1,'name':'snapshot-a.zip','size':100,'created_at':'2026'}]}),patch('persistence.gh',return_value=b.getvalue()),patch.dict('os.environ',{'GH_REPO':'example/repo'}):
            with self.assertRaises(RuntimeError):p.restore(self.root)

    def test_top10_only_and_eligible_count(self):
        rows=[row(vid=f'{i:011d}',count=i) for i in range(15)]
        previous=hist(*[row(vid=r['video_id'],count=0,at=NOW-timedelta(hours=24)) for r in rows])
        top,excluded,total=t.ranking(rows,previous,NOW)
        self.assertEqual((len(top),len(excluded),total),(10,0,15))
        self.assertEqual([r['view_delta'] for r in top],list(range(14,4,-1)))

    def test_batch_limit_and_individual_observation_times(self):
        members={f'{i:011d}':row(vid=f'{i:011d}') for i in range(51)}
        sizes=[]
        def call(endpoint,params):
            ids=params['id'].split(',');sizes.append(len(ids))
            return {'items':[dict(id=i,status={'privacyStatus':'public'},snippet={},statistics={}) for i in ids]}
        times=iter([NOW,NOW+timedelta(minutes=1)])
        rows,_=t.sample(members,call,lambda:next(times))
        self.assertEqual(sizes,[50,1])
        self.assertNotEqual(rows[0]['observed_at'],rows[-1]['observed_at'])

    def test_failure_after_discovery_does_not_save_partial(self):
        def call(endpoint,params):
            if endpoint=='videoCategories':return {'items':[]}
            if 'chart' in params:return {'items':[{'id':A}]}
            raise RuntimeError('detail failed')
        with self.assertRaises(RuntimeError):t.run(self.root,self.c,call,lambda:NOW)
        self.assertEqual(list(self.root.rglob('*.json')),[])

    def test_settings_bounds(self):
        for field,value in [('pool_limit',True),('regions',['us']),('categories_per_run',5),('search_budget',1),('request_budget',0)]:
            c=dict(self.c);c[field]=value
            path=self.root/'config.json';path.write_text(json.dumps(c))
            with self.assertRaises(ValueError):t.settings(path)

    def test_persistence_upload_verify_delete_and_restore_roundtrip(self):
        import zipfile
        t.atomic_json(self.root/'history'/NOW.strftime('%Y-%m-%d-quota.json'),{'attempts':[]})
        t.atomic_json(self.root/'history'/NOW.strftime('%Y-%m-%dT060000Z.json'),hist(row())[0])
        uploaded=[];events=[]
        def release():
            assets=[{'id':1,'name':'snapshot-old.zip'}]
            if uploaded:assets.append(dict(id=2,name=uploaded[0].name,size=uploaded[0].stat().st_size,created_at='2026'))
            return {'assets':assets}
        def gh(*args,**kwargs):
            events.append(args)
            if args[:2]==('release','upload'):uploaded.append(Path(args[3]));return ''
            return ''
        with patch('persistence.release',side_effect=release),patch('persistence.gh',side_effect=gh),patch.dict('os.environ',{'GITHUB_RUN_ID':'roundtrip','GH_REPO':'example/repo'}),patch('collector.cleanup'):
            p.save(self.root)
        self.assertEqual(events[0][:2],('release','upload'))
        self.assertIn('DELETE',events[1])
        raw=uploaded[0].read_bytes()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:self.assertEqual(len(z.namelist()),2)
        dest=self.root/'restored'
        asset=dict(id=2,name=uploaded[0].name,size=len(raw),created_at='2026')
        with patch('persistence.release',return_value={'assets':[asset]}),patch('persistence.gh',return_value=raw),patch.dict('os.environ',{'GH_REPO':'example/repo'}):
            p.restore(dest)
        self.assertEqual(len(list((dest/'history').glob('*.json'))),2)

    def test_size_or_verification_failure_does_not_delete_old(self):
        with patch('persistence.release',return_value={'assets':[{'id':1,'name':'snapshot-old.zip'}]}),patch('persistence.gh') as gh,patch.dict('os.environ',{'GITHUB_RUN_ID':'unverified'}):
            with self.assertRaises(RuntimeError):p.save(self.root)
            self.assertEqual(gh.call_count,1)


if __name__=='__main__':unittest.main()
