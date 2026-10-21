"""YouTube public metadata collector; Python standard library only."""
import argparse
import csv
import html
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CST = timezone(timedelta(hours=8))
FIELDS = ['rank', 'video_id', 'title', 'channel', 'published_at', 'view_count', 'regions', 'url']


def config(path):
    c = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(c.get('regions'), list) or not c['regions'] or any(not isinstance(r, str) or not re.fullmatch('[A-Z]{2}', r) for r in c['regions']):
        raise ValueError('regions must be a nonempty list of ISO two-letter uppercase codes')
    c['regions'] = list(dict.fromkeys(c['regions']))
    for k, lo, hi in [('pages_per_region', 1, 5), ('page_size', 1, 50), ('top_n', 1, 500), ('history_days', 1, 25)]:
        if type(c.get(k)) is not int or not lo <= c[k] <= hi:
            raise ValueError(f'{k} must be an integer in {lo}..{hi}')
    for k in ['include_keywords', 'exclude_keywords']:
        if not isinstance(c.get(k), list) or any(not isinstance(s, str) or not s.strip() for s in c[k]):
            raise ValueError(f'{k} must be a list of nonempty strings')
    return c


def request(params, key):
    # Header keeps the key out of URLs and exception messages.
    req = Request('https://www.googleapis.com/youtube/v3/videos?' + urlencode(params), headers={'X-Goog-Api-Key': key})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            status = exc.code
            if status != 429 and status < 500:
                raise RuntimeError(f'YouTube HTTP {status}; check API enablement, key restrictions, quota and region support') from None
        except (URLError, TimeoutError):
            status = 'network/timeout'
        if attempt < 2:
            time.sleep(2 ** attempt)
    raise RuntimeError(f'YouTube request failed after 3 attempts ({status})')


def collect(c, key, fetch=request):
    videos = {}
    for region in c['regions']:
        token = None
        for _ in range(c['pages_per_region']):
            params = {'part': 'snippet,statistics,status', 'chart': 'mostPopular', 'regionCode': region, 'maxResults': c['page_size']}
            if token:
                params['pageToken'] = token
            page = fetch(params, key)
            for v in page.get('items', []):
                if v.get('status', {}).get('privacyStatus') != 'public':
                    continue
                vid = v.get('id', '')
                if not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid):
                    continue
                s = v['snippet']
                text = (s.get('title', '') + ' ' + s.get('description', '')).casefold()
                if c['include_keywords'] and not any(k.casefold() in text for k in c['include_keywords']):
                    continue
                if any(k.casefold() in text for k in c['exclude_keywords']):
                    continue
                if vid not in videos:
                    count = v.get('statistics', {}).get('viewCount')
                    videos[vid] = {'video_id': vid, 'title': s.get('title', ''), 'channel': s.get('channelTitle', ''), 'published_at': s.get('publishedAt', ''), 'view_count': int(count) if count is not None else None, 'regions': [], 'url': 'https://www.youtube.com/watch?v=' + vid}
                if region not in videos[vid]['regions']:
                    videos[vid]['regions'].append(region)
            token = page.get('nextPageToken')
            if not token:
                break
    rows = sorted(videos.values(), key=lambda v: (-(v['view_count'] if v['view_count'] is not None else -1), v['video_id']))
    return [dict(rank=i, **v) for i, v in enumerate(rows[:c['top_n']], 1)]


def cleanup(root, now, days):
    cutoff = now.date() - timedelta(days=days-1)
    for folder in ['history', 'reports']:
        for f in (root / folder).glob('*'):
            try:
                day = datetime.strptime(f.stem, '%Y-%m-%d').date()
            except ValueError:
                continue
            if day < cutoff or day > now.date():
                f.unlink()


def cell(value):
    return html.escape(str(value), quote=True).replace('|', '&#124;').replace('\n', ' ').replace('\r', ' ').replace('[', '&#91;').replace(']', '&#93;').replace('`', '&#96;')


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def write(root, rows, now, regions, demo=False):
    day = now.strftime('%Y-%m-%d')
    for folder in ['history', 'reports']:
        (root / folder).mkdir(parents=True, exist_ok=True)
    payload = {'collected_at': now.isoformat(), 'demo': demo, 'regions': regions, 'videos': rows}
    (root / 'history' / (day + '.json')).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    with (root / 'reports' / (day + '.csv')).open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_safe(','.join(row[k]) if k == 'regions' else ('' if row[k] is None else row[k])) for k in FIELDS})
    lines = [f'# 世界漫游指南 · {day} 候选榜', '', '**模拟数据，仅验证流程。**' if demo else '来源：YouTube Data API v3；仅供人工选题。', '', f'采集时间：{now.isoformat()}；地区：{", ".join(regions)}', '', '按 API 原始累计播放量降序排列；并非当日播放增量或全球热度排名。地区为榜单市场，不是拍摄地。', '', '| 排名 | 视频 | 频道 | 累计播放量 | 地区 |', '| --- | --- | --- | --- | --- |']
    for r in rows:
        lines.append(f"| {r['rank']} | [{cell(r['title'])}]({r['url']}) | {cell(r['channel'])} | {r['view_count'] if r['view_count'] is not None else '未提供'} | {','.join(r['regions'])} |")
    if not rows:
        lines += ['', '本次无匹配候选；可调整关键词或地区，空榜不代表采集失败。']
    (root / 'reports' / (day + '.md')).write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--output', default='data')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    c = config(args.config)
    root = Path(args.output)
    now = datetime.now(CST)
    cleanup(root, now, c['history_days'])  # Prune even when the subsequent API run fails.
    if args.demo:
        from demo_data import fetch
        rows = collect(c, '', fetch)
    else:
        key = os.environ.get('YOUTUBE_API_KEY', '').strip()
        if not key:
            raise ValueError('Missing YOUTUBE_API_KEY environment variable; configure the GitHub Actions secret')
        rows = collect(c, key)
    write(root, rows, now, c['regions'], args.demo)
    print(f"{'DEMO' if args.demo else 'OK'}: {len(rows)} candidates; output={root}")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
