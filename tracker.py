"""V3 monitored-pool ranking. Public metadata only; no score or extrapolation."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import csv
import json
import os
from pathlib import Path
import re
import sys
from collector import cell, csv_safe, cleanup
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

UTC = timezone.utc
ID = re.compile(r'[A-Za-z0-9_-]{11}')


def instant(s):
    try:
        t = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return t.astimezone(UTC) if t.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def valid_count(n):
    return type(n) is int and n >= 0


def settings(path):
    c = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(c.get('regions'), list) or not 1 <= len(c['regions']) <= 12 or any(not isinstance(r, str) or not re.fullmatch('[A-Z]{2}', r) for r in c['regions']):
        raise ValueError('Invalid regions')
    c['regions'] = list(dict.fromkeys(c['regions']))
    for k, low, high in [('pool_limit', 50, 5000), ('tracking_days', 2, 24), ('categories_per_run', 1, 4), ('search_budget', 1, 24), ('request_budget', 10, 500)]:
        if type(c.get(k)) is not int or not low <= c[k] <= high:
            raise ValueError('Invalid ' + k)
    if len(c['regions']) * c['categories_per_run'] > c['search_budget']:
        raise ValueError('Discovery exceeds search budget')
    return c


class API:
    """No automatic retries: each outbound attempt is budgeted, errors are redacted."""
    def __init__(self, key, c, root=None):
        self.key, self.c, self.root = key, c, root
        self.calls = self.searches = 0

    def __call__(self, endpoint, params):
        if self.calls >= self.c['request_budget'] or (endpoint == 'search' and self.searches >= self.c['search_budget']):
            raise RuntimeError('API budget exhausted; run aborted without a partial ranking')
        if self.root is not None:
            now = datetime.now(UTC)
            usage = []
            for p in (self.root/'history').glob('*-quota.json'):
                usage.extend(json.loads(p.read_text(encoding='utf-8'))['attempts'])
            usage = [x for x in usage if instant(x['at']) and instant(x['at']) > now-timedelta(hours=24)]
            if len(usage) >= 400 or (endpoint == 'search' and sum(x['endpoint'] == 'search' for x in usage) >= 80):
                raise RuntimeError('Rolling 24-hour local quota guard reached')
            # Reserve before network I/O, including failed attempts. Single writer enforced in workflow.
            today = self.root/'history'/(now.strftime('%Y-%m-%d')+'-quota.json')
            attempts = json.loads(today.read_text(encoding='utf-8'))['attempts'] if today.exists() else []
            atomic_json(today, dict(attempts=attempts+[dict(at=now.isoformat(), endpoint=endpoint)]))
        self.calls += 1
        self.searches += endpoint == 'search'
        req = Request('https://www.googleapis.com/youtube/v3/' + endpoint + '?' + urlencode(params), headers={'X-Goog-Api-Key': self.key})
        try:
            with urlopen(req, timeout=30) as r:
                data = json.load(r)
            if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                raise ValueError('Invalid API response')
            return data
        except (HTTPError, URLError, TimeoutError, ValueError):
            raise RuntimeError('YouTube request failed; check quota, connectivity and key restrictions. No partial ranking saved.') from None


def discover(c, call, now):
    found = {}
    def add(vid, source):
        if isinstance(vid, str) and ID.fullmatch(vid):
            found.setdefault(vid, []).append(dict(source, discovered_at=now.isoformat()))
    slot = int(now.timestamp() // (6 * 3600))
    for region in c['regions']:
        page = call('videos', {'part': 'id', 'chart': 'mostPopular', 'regionCode': region, 'maxResults': 50})
        for v in page.get('items', []):
            add(v.get('id'), {'method': 'mostPopular', 'region': region, 'category': 'all'})
        categories = call('videoCategories', {'part': 'snippet', 'regionCode': region})
        ids = sorted({v['id'] for v in categories.get('items', []) if v.get('snippet', {}).get('assignable') and str(v.get('id', '')).isdigit()}, key=int)
        for j in range(min(len(ids), c['categories_per_run'])):
            category = ids[(slot * c['categories_per_run'] + j) % len(ids)]
            # No keyword, duration, language or editorial-topic filter. Category rotation bounds cost.
            page = call('search', {'part': 'snippet', 'type': 'video', 'regionCode': region,
                'videoCategoryId': category, 'order': 'viewCount', 'maxResults': 50,
                'publishedAfter': (now-timedelta(days=30)).isoformat(), 'safeSearch': 'none'})
            for v in page.get('items', []):
                add(v.get('id', {}).get('videoId'), {'method': 'categorySearch', 'region': region, 'category': category})
    return found


def load_history(root, now):
    result = []
    for p in sorted((root/'history').glob('*.json')):
        data = json.loads(p.read_text(encoding='utf-8'))  # Corruption fails closed.
        if data.get('schema') != 3 or data.get('demo'):
            continue  # V1/V2 and synthetic history cannot become V3 baselines.
        t = instant(data.get('collected_at'))
        if not t:
            raise ValueError('Invalid snapshot timestamp')
        if now-timedelta(days=25) <= t < now:
            result.append(data)
    return sorted(result, key=lambda d: instant(d['collected_at']))


def pool(history, discovered, now, c):
    existing = {}
    for snap in history:
        for v in snap['videos']:
            if instant(v.get('last_discovered_at')) and instant(v['last_discovered_at']) >= now-timedelta(days=c['tracking_days']):
                existing[v['video_id']] = v
    # Stable membership: keep tracked IDs, then deterministic ID order for new admissions.
    dropped = 0
    for vid, sources in sorted(discovered.items()):
        if vid not in existing and len(existing) >= c['pool_limit']:
            dropped += 1
            continue
        old = existing.get(vid, {})
        def source_key(s):
            return (s['method'], s['region'], s['category'])
        combined = {source_key(s): s for s in old.get('sources', []) if instant(s.get('discovered_at')) and instant(s['discovered_at']) >= now-timedelta(days=25)}
        combined.update({source_key(s): s for s in sources})
        existing[vid] = dict(old, video_id=vid, sources=list(combined.values()), last_discovered_at=now.isoformat())
    if len(existing) > c['pool_limit']:
        raise ValueError('Pool limit reduced below existing membership; adjust configuration explicitly')
    return existing, dropped


def sample(members, call, clock):
    rows, unavailable = [], set()
    ids = sorted(members)
    for offset in range(0, len(ids), 50):
        batch = ids[offset:offset+50]
        page = call('videos', {'part': 'snippet,statistics,status', 'id': ','.join(batch), 'maxResults': 50})
        observed = clock().astimezone(UTC).isoformat()
        accepted = set()
        for v in page.get('items', []):
            vid = v.get('id')
            if vid not in batch or v.get('status', {}).get('privacyStatus') != 'public':
                continue
            accepted.add(vid)
            s = v.get('snippet', {})
            count = v.get('statistics', {}).get('viewCount')
            count = int(count) if isinstance(count, (int, str)) and not isinstance(count, bool) and str(count).isdigit() else None
            rows.append(dict(members[vid], title=s.get('title', ''), channel=s.get('channelTitle', ''),
                published_at=s.get('publishedAt'), category=s.get('categoryId'), view_count=count,
                observed_at=observed, url='https://www.youtube.com/watch?v='+vid))
        unavailable.update(set(batch)-accepted)
    return rows, unavailable


def ranking(rows, history, now):
    index = {}
    for snap in history:
        for row in snap['videos']:
            index.setdefault(row['video_id'], []).append(row)
    eligible, excluded = [], []
    for row in rows:
        r = dict(row)
        t, pub = instant(r.get('observed_at')), instant(r.get('published_at'))
        reason = None
        if not t or not 0 <= (now-t).total_seconds() <= 900:
            reason = '当前快照过期或时间无效'
        elif not valid_count(r.get('view_count')):
            reason = '当前播放量缺测'
        elif not pub or pub > t:
            reason = '发布时间无效'
        elif pub > now-timedelta(hours=24):
            reason = '新视频未满24小时；不以零作基线'
        candidates = []
        if not reason:
            for old in index.get(r['video_id'], []):
                b = instant(old.get('observed_at'))
                if b and pub <= b < t and valid_count(old.get('view_count')) and abs((b-(now-timedelta(hours=24))).total_seconds()) <= 1800 and abs((t-b).total_seconds()-86400) <= 1800:
                    candidates.append(old)
            if not candidates:
                reason = '缺少24小时附近有效基线；不外推'
        if not reason:
            old = min(candidates, key=lambda x: (abs((t-instant(x['observed_at'])).total_seconds()-86400), x['observed_at']))
            # A visible correction anywhere in the window invalidates its monotonic difference.
            series = [old] + [x for x in index.get(r['video_id'], []) if instant(x.get('observed_at')) and instant(old['observed_at']) < instant(x['observed_at']) < t and valid_count(x.get('view_count'))] + [r]
            series.sort(key=lambda x: instant(x['observed_at']))
            if any(a['view_count'] > b['view_count'] for a, b in zip(series, series[1:])):
                reason = '窗口内播放量回退；等待干净基线'
            else:
                hours = (t-instant(old['observed_at'])).total_seconds()/3600
                r.update(baseline_at=old['observed_at'], baseline_view_count=old['view_count'],
                    view_delta=r['view_count']-old['view_count'], interval_hours=hours,
                    window_error_minutes=(hours-24)*60, endpoint_age_minutes=(now-t).total_seconds()/60)
        if reason:
            excluded.append(dict(r, exclusion_reason=reason))
        else:
            eligible.append(r)
    eligible.sort(key=lambda r: (-r['view_delta'], r['video_id']))
    return [dict(r, rank=i) for i, r in enumerate(eligible[:10], 1)], excluded, len(eligible)


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def purge_unavailable(root, history, unavailable):
    if not unavailable:
        return
    for snap in history:
        snap['videos'] = [v for v in snap['videos'] if v['video_id'] not in unavailable]
    for p in (root/'history').glob('*.json'):
        data = json.loads(p.read_text(encoding='utf-8'))
        data['videos'] = [v for v in data.get('videos', []) if v['video_id'] not in unavailable]
        atomic_json(p, data)
    # Previous reports may contain withdrawn metadata, so remove and regenerate current report.
    for p in (root/'reports').glob('*'):
        if p.is_file():
            p.unlink()


def report(root, top, excluded, total, now, stats):
    folder = root/'reports'
    folder.mkdir(parents=True, exist_ok=True)
    stem = now.strftime('%Y-%m-%dT%H%M%S%fZ')
    payload = dict(schema=3, ranked_at=now.isoformat(), scope='监测候选池 TOP10，非官方全球前十',
        metric='公开累计播放量快照差值，约24小时；未经外推', stats=stats,
        comparable_count=total, videos=top, excluded=excluded)
    atomic_json(folder/(stem+'.json'), payload)
    fields = ['rank','video_id','title','channel','published_at','category','sources','baseline_at','observed_at','baseline_view_count','view_count','view_delta','interval_hours','window_error_minutes','endpoint_age_minutes','url']
    with (folder/(stem+'.csv')).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in top:
            w.writerow({k: csv_safe(json.dumps(r.get(k), ensure_ascii=False) if k == 'sources' else r.get(k, '')) for k in fields})
    lines = ['# 世界漫游指南｜监测池过去24小时新增播放量 TOP10', '',
        '**非 YouTube 官方榜单，非全球前十。仅比较本监测池内具有合格快照的视频，窗口允许±30分钟误差。**',
        f'榜单时刻：{now.isoformat()}；来源：YouTube Data API v3。',
        f'有效可比 {total} 条；展示 {len(top)} 条；缺测/不合格 {len(excluded)} 条。',
        '同增量按视频ID排序；不按类型、知识评分、频道或地区调整名次；不补足十条。',
        '公开播放计数可能延迟或修订；发现市场不代表播放来源、作者国籍或拍摄地。',
        '以下是中文公众号待审核素材，原题保留原文；请观看原视频并核对事实与版权后编辑发布。', '']
    for r in top:
        lines += [f"## {r['rank']}. {cell(r['title'])}", f"[原视频]({r['url']})｜频道：{cell(r['channel'])}",
            f"本地计算新增播放量：{r['view_delta']:,}；累计：{r['view_count']:,}；基线累计：{r['baseline_view_count']:,}。",
            f"快照：{r['baseline_at']} → {r['observed_at']}；间隔 {r['interval_hours']:.3f} 小时；窗口误差 {r['window_error_minutes']:+.1f} 分钟。",
            '发现来源：'+cell(json.dumps(r['sources'], ensure_ascii=False)), '']
    if not top:
        lines += ['当前无合格榜单。冷启动通常需要至少24小时；调度延迟或采集失败也可能导致空榜。', '']
    lines += ['## 覆盖与缺测说明', cell(json.dumps(stats, ensure_ascii=False)),
        cell(json.dumps(dict(Counter(r['exclusion_reason'] for r in excluded)), ensure_ascii=False)), '',
        '## 可选：热度之外（不参与排名）',
        '围绕上榜视频人工追问：这类公开播放计数如何定义？其文化、技术或历史背景是什么？',
        '这些仅是背景知识选题提示，不代表已验证视频内容。请为选题另行查证权威来源。', '']
    (folder/(stem+'.md')).write_text('\n'.join(lines), encoding='utf-8')


def run(root, c, call, clock=lambda: datetime.now(UTC)):
    started = clock().astimezone(UTC)
    cleanup(root, started, 7)
    history = load_history(root, started)
    found = discover(c, call, started)
    members, dropped = pool(history, found, started, c)
    rows, unavailable = sample(members, call, clock)
    now = clock().astimezone(UTC)
    purge_unavailable(root, history, unavailable)
    top, excluded, total = ranking(rows, history, now)
    stats = dict(discovered=len(found), tracked=len(members), pool_cap=c['pool_limit'],
        not_admitted=dropped, unavailable=len(unavailable), regions=c['regions'],
        categories_per_market_this_run=c['categories_per_run'], calls=getattr(call, 'calls', None),
        search_calls=getattr(call, 'searches', None), collection_started_at=started.isoformat())
    atomic_json(root/'history'/(now.strftime('%Y-%m-%dT%H%M%S%fZ')+'.json'),
        dict(schema=3, demo=False, collected_at=now.isoformat(), videos=rows))
    report(root, top, excluded, total, now, stats)
    return top


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', default='tracker_config.json')
    p.add_argument('--output', default='data')
    args = p.parse_args()
    c = settings(args.config)
    if os.environ.get('YOUTUBE_ANALYTICS_APPROVAL_CONFIRMED') != 'true':
        raise ValueError('Confirm applicable YouTube analytics approval/amendment before live use; see docs/QUOTA_COMPLIANCE.md')
    key = os.environ.get('YOUTUBE_API_KEY', '').strip()
    if not key:
        raise ValueError('Missing YOUTUBE_API_KEY environment variable')
    top = run(Path(args.output), c, API(key, c, Path(args.output)))
    print(f'OK: {len(top)} comparable ranked videos')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError, TypeError):
        sys.exit('V3 failed; no partial ranking published. Check config, quota, data store and connectivity. No secrets logged.')
